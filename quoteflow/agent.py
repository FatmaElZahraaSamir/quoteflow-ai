"""The quoting agent.

LLMAgent  - an LLM reads the email and decides which tools to call (function calling): look up the
            customer, search the catalog, check stock, price the order. It then returns a small JSON
            "understanding". Usually 3 model calls per email, which fits free tiers.
RulesAgent - the same tools driven by rules (extract.py). Used for the no-key demo, for tests and
            as an automatic fallback if the model fails.

Both produce an AgentResult; prices and the reply's figures always come from pricing.py.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from decimal import Decimal, InvalidOperation

from .catalog import Catalog
from .compose import AgentResult, build_result
from .data import Store
from .emails import Email
from .extract import RequestedLine, Understanding, understand
from .llm import LLMError
from .tools import TOOL_SPECS, Toolbox

SYSTEM_PROMPT = """You are the sales assistant of {company}, a B2B office-supplies distributor in the UAE. \
You read one customer email and prepare a quotation that a person will approve before it is sent.

How to work:
1. Call lookup_customer with the sender's email.
2. Call search_catalog once for each product the customer mentions. You can call several tools at once.
3. Call check_stock for each matched product, with the quantity in the product's unit of sale. Convert counts \
of single items into units of sale (1,000 envelopes sold in boxes of 500 = 2 boxes).
4. Call calculate_quote with all lines, the customer's email and the delivery city.
5. Then answer with ONLY a JSON object (no code fences) with these keys:
   "intent": "quote_request" or "other"
   "route_to": "sales", "accounts" or "customer service"
   "lines": [{{"sku": "...", "quantity": 1, "requested_as": "the customer's words"}}]
   "not_found": [products the customer asked for that we don't sell]
   "unclear": [{{"requested_as": "...", "options": ["SKU", ...], "missing": "quantity" or "product"}}]
   "delivery_city": UAE city or emirate, or null
   "requested_discount_percent": number, only if the customer asks for a specific discount, else null
   "deadline": "YYYY-MM-DD" if the customer needs delivery by a date, else null
   "note": 2-3 friendly sentences for the reply in the customer's language. No prices, totals or discount \
amounts: the system adds the priced quotation under your note.

Rules: never invent products, SKUs, prices or stock. Use only products that search_catalog returned with a \
score of 0.6 or more. If the email is not a quote request, don't call tools: return intent "other", an empty \
"lines" list, who should handle it in "route_to", and a note saying that team will reply.
Today is {today}."""


class RulesAgent:
    mode = "rules"
    label = "Rules (demo mode, no API key)"

    def __init__(self, store: Store, today: date | None = None, catalog: Catalog | None = None):
        self.store = store
        self.today = today or date.today()
        self.catalog = catalog or Catalog(store)

    def run(self, email: Email, quote_number: str = "DRAFT") -> AgentResult:
        started = time.perf_counter()
        toolbox = Toolbox(self.store, self.today, self.catalog)
        u = understand(email.subject, email.body, self.catalog, self.today)
        if u.intent == "quote_request":  # the same tool calls an LLM would make, so the trace looks the same
            toolbox.call("lookup_customer", {"email": email.sender_email}, step=1)
            for line in u.lines:
                toolbox.call("search_catalog", {"query": _item_words(line.requested_as)}, step=1)
            for name in u.not_found:
                toolbox.call("search_catalog", {"query": name}, step=1)
            for line in u.lines:
                toolbox.call("check_stock", {"sku": line.sku, "quantity": line.quantity}, step=2)
            if u.lines:
                toolbox.call("calculate_quote", {"lines": [{"sku": l.sku, "quantity": l.quantity} for l in u.lines],
                                                 "customer_email": email.sender_email,
                                                 "delivery_city": u.delivery_city or ""}, step=2)
        result = build_result(email, u, self.store, mode=self.mode, quote_number=quote_number, today=self.today,
                              tool_calls=toolbox.calls)
        result.seconds = round(time.perf_counter() - started, 3)
        return result


class LLMAgent:
    mode = "llm"

    def __init__(self, llm, store: Store, today: date | None = None, catalog: Catalog | None = None,
                 max_steps: int = 6, fallback: bool = True):
        self.llm = llm
        self.store = store
        self.today = today or date.today()
        self.catalog = catalog or Catalog(store)
        self.max_steps = max_steps
        self.fallback = fallback

    @property
    def label(self) -> str:
        return f"{getattr(self.llm, 'label', 'LLM')} · {getattr(self.llm, 'model', '')}"

    def run(self, email: Email, quote_number: str = "DRAFT") -> AgentResult:
        started = time.perf_counter()
        toolbox = Toolbox(self.store, self.today, self.catalog)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT.format(company=self.store.rules.company,
                                                               today=self.today.isoformat())},
            {"role": "user", "content": f"From: {email.sender}\nSubject: {email.subject}\n\n{email.body}"},
        ]
        llm_calls, final, warnings, asked_again = 0, None, [], False
        try:
            for step in range(1, self.max_steps + 1):
                turn = self.llm.chat_tools(messages, TOOL_SPECS)
                llm_calls += 1
                messages.append(turn.message)
                if turn.tool_calls:
                    for call in turn.tool_calls:
                        result = toolbox.call(call.name, call.arguments, step=step)
                        messages.append({"role": "tool", "tool_call_id": call.id,
                                         "content": json.dumps(result, ensure_ascii=False)})
                    continue
                final = parse_final(turn.content)
                if final is not None:
                    break
                if asked_again:
                    break
                asked_again = True
                messages.append({"role": "user", "content": "Reply with the JSON object only, using the keys "
                                                            "described in the instructions."})
        except LLMError as exc:
            warnings.append(f"The AI model failed ({exc}).")

        if final is None:
            if not self.fallback:
                raise LLMError("The model did not return a usable answer. " + " ".join(warnings))
            warnings.append("The AI model did not finish, so the rules engine prepared this draft.")
            result = RulesAgent(self.store, self.today, self.catalog).run(email, quote_number)
            result.warnings = warnings + result.warnings
            result.llm_calls = llm_calls
            result.tool_calls = toolbox.calls + result.tool_calls
            result.seconds = round(time.perf_counter() - started, 3)
            return result

        understanding, check_warnings = to_understanding(final, self.store, self.today)
        result = build_result(email, understanding, self.store, mode=self.mode, quote_number=quote_number,
                              today=self.today, note=str(final.get("note") or ""), tool_calls=toolbox.calls,
                              warnings=warnings + check_warnings)
        result.llm_calls = llm_calls
        result.seconds = round(time.perf_counter() - started, 3)
        return result


# ---------------------------------------------------------------------- helpers
def parse_final(text: str) -> dict | None:
    """The JSON object in the model's last message (tolerates code fences and text around it)."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) and "intent" in value else None


def to_understanding(final: dict, store: Store, today: date) -> tuple[Understanding, list[str]]:
    """Validate the model's answer against the real catalog. Anything invalid is dropped and flagged."""
    warnings: list[str] = []
    lines: list[RequestedLine] = []
    for raw in final.get("lines") or []:
        sku = str((raw or {}).get("sku", "")).strip().upper()
        try:
            quantity = int(raw.get("quantity"))
        except (TypeError, ValueError):
            quantity = 0
        if sku not in store.products:
            warnings.append(f"Check: the AI suggested an unknown product code '{sku}', so it was left out.")
            continue
        if quantity < 1:
            warnings.append(f"Check: no valid quantity for {sku}, so it was left out.")
            continue
        lines.append(RequestedLine(sku, quantity, str(raw.get("requested_as") or "")))
    merged: dict[str, RequestedLine] = {}
    for line in lines:
        if line.sku in merged:
            merged[line.sku].quantity += line.quantity
        else:
            merged[line.sku] = line

    city = final.get("delivery_city")
    if city:
        city = store.find_city(str(city)) or str(city).strip()
    discount = None
    if final.get("requested_discount_percent") not in (None, "", 0):
        try:
            discount = Decimal(str(final["requested_discount_percent"]).strip("% ")) / 100
        except InvalidOperation:
            discount = None
    deadline = None
    if final.get("deadline"):
        try:
            deadline = date.fromisoformat(str(final["deadline"])[:10])
        except ValueError:
            deadline = None
    intent = "quote_request" if final.get("intent") == "quote_request" else "other"
    unclear = [u for u in (final.get("unclear") or []) if isinstance(u, dict) and u.get("requested_as")]
    return Understanding(intent=intent, route_to=str(final.get("route_to") or "sales"),
                         lines=list(merged.values()), not_found=[str(x) for x in final.get("not_found") or []],
                         unclear=unclear, delivery_city=city or None, requested_discount=discount,
                         deadline=deadline), warnings


def _item_words(requested_as: str) -> str:
    """'40 boxes of A4 copy paper' -> 'A4 copy paper' (what the agent searches for)."""
    text = re.sub(r"^\s*(?:\d[\d,]*|an?)\s*(?:x|×|nos?)?\s*", "", requested_as, flags=re.I)
    return re.sub(r"^(?:boxes|box|packs|pack|cartons|carton|cases|case|rolls|roll|sets|set)\s+of\s+", "", text,
                  flags=re.I)
