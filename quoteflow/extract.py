"""Rule-based reading of a quote request, used by the offline demo mode (no API key).

It finds "quantity + product" phrases in the many ways people write them:

    - 40 boxes of A4 copy paper            2 x HP 26A toner          10 nos HDMI cables 2m
    one paper shredder, an A3 laminator     half a dozen staplers     1,000 DL envelopes (= 2 boxes of 500)

An LLM does this more flexibly; these rules make the demo, the tests and a no-cost fallback possible.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .catalog import PACK_WORDS, Catalog, singular

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
                "fifty": 50, "hundred": 100}
PHRASE_NUMBERS = [("half a dozen", "6"), ("a dozen", "12"), ("one dozen", "12"), ("a couple of", "2"),
                  ("a pair of", "2"), ("a hundred", "100")]
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], start=1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})

_ANCHOR = re.compile(
    r"(?<![\w+\-/.,])(?P<amount>\d{1,5})\s?(?P<measure>kg|kilos?|l|litres?|liters?)\s+of(?=\s)"  # 6 kg of coffee
    r"|(?<![\w+\-/.,])(?P<qty>\d{1,6})(?:[x×](?=\s))?(?![\w%])"  # a standalone number (or "2x"), not INV-2291 / 3D
    r"(?!\s*(?:%|ml|l|kg|g|cm|mm|m|oz|gsm|gb|tb|inch|inches)\b)"  # ... and not a size: 15 %, 500 ml
    r"|(?<![\w'])(?P<article>an?)(?=\s)", re.I)          # "a" / "an" (only kept for clear product matches)
_FILLER = re.compile(r"^\s*(?:x|×|nos?\.?|pcs\.?|pieces?|units?|qty)(?:\s+of)?\s+", re.I)
_UNIT_OF = re.compile(rf"^\s*(?P<unit>{'|'.join(sorted(PACK_WORDS, key=len, reverse=True))})\s+of\s+", re.I)
_TRAILING = re.compile(r"\s+(?:for|to|by|in|at|from|delivered|next|this|please|asap|urgently|each)\b.*$", re.I)
_SELL_QUESTION = re.compile(r"\b(?:do|does|can) you (?:also )?(?:sell|have|stock|carry|supply)\s+(?P<item>[^?.!\n]+)",
                            re.I)
_DISCOUNT = re.compile(r"(?P<pct>\d{1,2}(?:\.\d+)?)\s?%\s*(?:off|discount)|discount of (?P<pct2>\d{1,2})\s?%", re.I)
_DEADLINE = re.compile(r"\bby\s+(?:(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+(?P<month>[a-z]+)|(?P<month2>[a-z]+)\s+"
                       r"(?P<day2>\d{1,2})(?:st|nd|rd|th)?)\b", re.I)
_QUOTE_WORDS = re.compile(r"\b(quote|quotation|price|prices|pricing|cost|how much|order|need|buy|purchase)\b", re.I)
_ACCOUNTS_WORDS = re.compile(r"\b(invoice|charged|refund|payment|statement|billing|receipt|overpaid)\b", re.I)
_SERVICE_WORDS = re.compile(r"\b(damaged|broken|late|missing|complaint|where is my order|not delivered|wrong item)\b",
                            re.I)


@dataclass
class RequestedLine:
    sku: str
    quantity: int
    requested_as: str
    conversion: str = ""
    score: float = 1.0


@dataclass
class Understanding:
    """What the customer asked for, in the same shape the LLM agent returns."""
    intent: str = "quote_request"            # or "other"
    route_to: str = "sales"                  # "accounts" / "customer service" for other emails
    lines: list[RequestedLine] = field(default_factory=list)
    not_found: list[str] = field(default_factory=list)
    unclear: list[dict] = field(default_factory=list)  # {"requested_as": ..., "options": [sku, ...]}
    delivery_city: str | None = None
    requested_discount: Decimal | None = None
    deadline: date | None = None


def normalise_numbers(text: str) -> str:
    text = re.sub(r"(\d),(\d{3})\b", r"\1\2", text)
    for phrase, digits in PHRASE_NUMBERS:
        text = re.sub(rf"\b{phrase}\b", digits, text, flags=re.I)
    return re.sub(rf"\b({'|'.join(NUMBER_WORDS)})\b(?=\s+[a-z0-9])(?!\s+of\s+(?:the|our|my|your|them|these|those)\b)",
                  lambda m: str(NUMBER_WORDS[m.group(1).lower()]), text, flags=re.I)


def find_lines(body: str, catalog: Catalog) -> tuple[list[RequestedLine], list[str], list[dict]]:
    """Return (matched lines, products we don't sell, unclear requests)."""
    text = normalise_numbers(body)
    lines: list[RequestedLine] = []
    not_found: list[str] = []
    unclear: list[dict] = []
    for segment in re.split(r"\n+|(?<=[.!?])\s+", text):
        anchors = list(_ANCHOR.finditer(segment))
        for i, anchor in enumerate(anchors):
            end = anchors[i + 1].start() if i + 1 < len(anchors) else len(segment)
            item = re.split(r"[,;:!?]|\.(?:\s|$)", segment[anchor.end():end])[0]
            item = re.sub(r"\s+(?:and|&|plus)\s*$", "", item.strip())
            unit_word = None
            item = _FILLER.sub("", item)
            unit = _UNIT_OF.match(item)
            if unit:
                unit_word, item = unit.group("unit").lower(), item[unit.end():]
            item = _balance(_TRAILING.sub("", item).strip(" -"))
            if not item or not re.search(r"[a-z]", item, re.I):
                continue
            item, leftover = _split_list(item, catalog)
            weak = anchor.group("article") is not None
            measure = anchor.group("measure")
            quantity = 1 if weak else int(anchor.group("amount") if measure else anchor.group("qty"))
            match, candidates = catalog.resolve(item)
            requested_as = f"{anchor.group(0)} {unit_word + ' of ' if unit_word else ''}{item}".strip()
            if match and measure:
                line = _by_measure(match, quantity, measure, requested_as)
                if line:
                    lines.append(line)
                else:
                    unclear.append({"requested_as": requested_as, "options": [match.product.sku],
                                    "missing": "quantity"})
            elif match and (not weak or match.score >= 0.8):
                lines.append(_line(match, quantity, unit_word, item, requested_as))
            elif candidates and not weak:
                unclear.append({"requested_as": requested_as, "options": [c.product.sku for c in candidates],
                                "missing": "product"})
            elif not weak and quantity < 10000 and _looks_like_product(item):
                not_found.append(item)
            if leftover:  # "... and some chairs": a product without a quantity
                match, candidates = catalog.resolve(leftover)
                options = [match.product.sku] if match else [c.product.sku for c in candidates]
                if options:
                    unclear.append({"requested_as": leftover, "options": options,
                                    "missing": "quantity" if match else "quantity and product"})
    for question in _SELL_QUESTION.finditer(text):  # "do you sell 3D printers?"
        item = _TRAILING.sub("", question.group("item")).strip()
        if catalog.best(item) is None and item and item not in not_found:
            not_found.append(item)
    return _merge(lines), not_found, unclear


def _line(match, quantity: int, unit_word: str | None, item: str, requested_as: str) -> RequestedLine:
    """'1,000 envelopes' of a product sold in boxes of 500 becomes 2 boxes."""
    product = match.product
    conversion = ""
    head = singular(item.lower().split()[-1]) if item.split() else ""
    if unit_word is None and product.pack_size > 1 and head == product.item:
        packs = math.ceil(quantity / product.pack_size)
        plural = product.unit + ("es" if product.unit.endswith(("x", "s", "ch")) else "s")
        conversion = f"{quantity:,} {head}s = {packs} {product.unit if packs == 1 else plural} of {product.pack_size:,}"
        quantity = packs
    return RequestedLine(product.sku, quantity, requested_as, conversion, match.score)


def _by_measure(match, amount: int, measure: str, requested_as: str) -> RequestedLine | None:
    """'6 kg of coffee beans' sold in 1 kg bags -> 6 bags; '10 litres of sanitizer' in 5 L refills -> 2."""
    unit = "kg" if measure.lower().startswith("k") else "l"
    size = re.search(rf"(\d+(?:\.\d+)?)\s*{unit}\b", match.product.name.lower())
    if not size:
        return None
    per_unit = float(size.group(1))
    count = math.ceil(amount / per_unit)
    word = match.product.item if match.product.unit == "each" else match.product.unit
    noun = word if count == 1 else word + ("es" if word.endswith(("x", "s", "ch")) else "s")
    conversion = f"{amount} {unit} = {count} {noun} of {size.group(1)} {unit.upper() if unit == 'l' else unit}"
    return RequestedLine(match.product.sku, count, requested_as, conversion, match.score)


def _split_list(item: str, catalog: Catalog) -> tuple[str, str]:
    """'blue pens and some chairs' -> ('blue pens', 'some chairs'); 'keyboard and mouse sets' stays whole."""
    parts = re.split(r"\s+(?:and|&|plus)\s+", item, maxsplit=1)
    if len(parts) == 1:
        return item, ""
    whole = catalog.best(item)
    if whole and whole.score >= 0.9:
        return item, ""
    first = catalog.best(parts[0])
    if first and first.score >= 0.8:
        leftover = re.sub(r"^(?:some|a few|several|also|the)\s+", "", parts[1].strip(), flags=re.I)
        return parts[0], leftover
    return item, ""


def _balance(item: str) -> str:
    """Drop a dangling bracket: 'refills (5L' -> 'refills (5L)', '(500 ml' -> '500 ml'."""
    if item.count("(") > item.count(")"):
        item = item + ")" if re.search(r"\(\S", item) and not item.startswith("(") else item.replace("(", "")
    elif item.count(")") > item.count("("):
        item = item.replace(")", "")
    return item.strip()


def _looks_like_product(item: str) -> bool:
    """Skip dates, times and other numbers that are not orders ("15 October", "2 weeks")."""
    first = item.lower().split()[0]
    return first not in MONTHS and first not in {"days", "day", "weeks", "week", "months", "month", "years", "pm",
                                                 "am", "hours", "minutes", "people", "guests", "branches", "staff"}


def _merge(lines: list[RequestedLine]) -> list[RequestedLine]:
    merged: dict[str, RequestedLine] = {}
    for line in lines:
        if line.sku in merged:
            merged[line.sku].quantity += line.quantity
            merged[line.sku].requested_as += f" + {line.requested_as}"
        else:
            merged[line.sku] = line
    return list(merged.values())


def find_discount(text: str) -> Decimal | None:
    match = _DISCOUNT.search(text)
    if not match:
        return None
    return Decimal(match.group("pct") or match.group("pct2")) / 100


def find_deadline(text: str, today: date) -> date | None:
    for match in _DEADLINE.finditer(text):
        month = MONTHS.get((match.group("month") or match.group("month2") or "").lower())
        day = int(match.group("day") or match.group("day2") or 0)
        if month and 1 <= day <= 31:
            try:
                deadline = date(today.year, month, day)
            except ValueError:
                continue
            return deadline if deadline >= today else date(today.year + 1, month, day)
    return None


def understand(subject: str, body: str, catalog: Catalog, today: date) -> Understanding:
    lines, not_found, unclear = find_lines(body, catalog)
    text = f"{subject}\n{body}"
    result = Understanding(lines=lines, not_found=not_found, unclear=unclear,
                           delivery_city=catalog.store.find_city(body),
                           requested_discount=find_discount(text), deadline=find_deadline(text, today))
    service_or_billing = _SERVICE_WORDS.search(text) or _ACCOUNTS_WORDS.search(text)
    asks_for_products = bool(lines or (unclear and not service_or_billing)
                             or (not_found and _QUOTE_WORDS.search(text) and not service_or_billing))
    if not asks_for_products:
        result.intent = "other"
        result.route_to = ("accounts" if _ACCOUNTS_WORDS.search(text) else
                           "customer service" if _SERVICE_WORDS.search(text) else "sales")
    return result
