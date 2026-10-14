"""Product search that copes with how customers actually write: plurals, typos, '5L' vs '5 L', synonyms.

Each product is matched through its name and aliases. The score is an F1 between the words of the
request and the words of the best matching phrase, weighted by how rare each word is in the catalog:

* "whiteboard"          -> Magnetic whiteboard (1.0) beats Whiteboard markers (0.67)
* "blue ballpoint pens" -> blue pens (1.0) beats black pens (0.6)
* "3D printers"         -> nothing above the threshold, so the agent says we don't sell them
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from .data import Product, Store

STOP = {"a", "an", "the", "of", "and", "with", "for", "in", "x", "nos", "no", "pcs", "pc", "units", "unit", "piece",
        "pieces", "please", "some", "our", "new", "your", "best", "price", "prices", "quote", "set", "sets"}
PACK_WORDS = {"box", "boxes", "pack", "packs", "carton", "cartons", "case", "cases", "roll", "rolls", "bag", "bags",
              "ream", "reams", "set", "sets", "pair", "pairs"}
MATCH_THRESHOLD = 0.62


@dataclass
class Match:
    product: Product
    score: float
    matched_phrase: str


def normalise(text: str) -> str:
    text = text.lower().replace("–", "-")
    text = re.sub(r"(\d)\s+(l|ml|kg|g|m|cm|mm|oz|gsm)\b", r"\1\2", text)  # "5 L" -> "5l", "8 oz" -> "8oz"
    text = re.sub(r"(\d),(\d{3})", r"\1\2", text)                          # "1,000" -> "1000"
    return re.sub(r"[^a-z0-9%]+", " ", text).strip()


def singular(word: str) -> str:
    if len(word) <= 3 or word.endswith(("ss", "us", "is")):
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "xes", "sses")):
        return word[:-2]
    if word.endswith("s"):
        return word[:-1]
    return word


def words(text: str) -> list[str]:
    return [singular(w) for w in normalise(text).split() if w not in STOP]


class Catalog:
    def __init__(self, store: Store):
        self.store = store
        self.phrases: list[tuple[Product, str, list[str]]] = []
        for product in store.products.values():
            for phrase in (product.name, *product.aliases):
                self.phrases.append((product, phrase, words(phrase)))
        documents = [{w for product, _, phrase_words in self.phrases if product.sku == sku for w in phrase_words}
                     for sku in store.products]
        n = len(documents)
        vocabulary = set().union(*documents)
        self.idf = {w: math.log((n + 1) / (sum(w in d for d in documents) + 0.5)) for w in vocabulary}
        self.unknown_weight = max(self.idf.values()) * 1.2

    def _weight(self, word: str) -> float:
        return self.idf.get(word, self.unknown_weight)

    def _same(self, query_word: str, phrase_word: str) -> bool:
        if query_word == phrase_word:
            return True
        # Typos ("ergonmic") may match a close catalog word, but a real catalog word ("staple") never
        # matches a different one ("stapler").
        if query_word in self.idf or len(query_word) < 5 or len(phrase_word) < 5:
            return False
        return fuzz.ratio(query_word, phrase_word) >= 85

    def score(self, query_words: list[str], phrase_words: list[str]) -> float:
        if not query_words or not phrase_words:
            return 0.0
        q_hit = sum(self._weight(q) for q in set(query_words) if any(self._same(q, p) for p in phrase_words))
        p_hit = sum(self._weight(p) for p in set(phrase_words) if any(self._same(q, p) for q in query_words))
        recall = q_hit / sum(self._weight(q) for q in set(query_words))
        precision = p_hit / sum(self._weight(p) for p in set(phrase_words))
        return 0.0 if recall + precision == 0 else 2 * recall * precision / (recall + precision)

    def search(self, query: str, limit: int = 3) -> list[Match]:
        q = [w for w in words(query) if w not in {singular(p) for p in PACK_WORDS} or len(words(query)) == 1]
        best: dict[str, Match] = {}
        for product, phrase, phrase_words in self.phrases:
            s = self.score(q, phrase_words)
            if product.sku not in best or s > best[product.sku].score:
                best[product.sku] = Match(product, round(s, 3), phrase)
        ranked = sorted(best.values(), key=lambda m: (-m.score, m.product.sku))
        return ranked[:limit]

    def resolve(self, query: str) -> tuple[Match | None, list[Match]]:
        """The confident match, or None plus the close candidates when the request is ambiguous ("pens")."""
        matches = self.search(query, limit=3)
        if not matches or matches[0].score < 0.5:
            return None, []
        top, rest = matches[0], matches[1:]
        close = [m for m in rest if m.score >= 0.5 and top.score - m.score < 0.1]
        if not close:
            return (top, []) if top.score >= MATCH_THRESHOLD else (None, [])
        return None, [top, *close]  # several products fit equally well: ask the customer

    def best(self, query: str) -> Match | None:
        return self.resolve(query)[0]
