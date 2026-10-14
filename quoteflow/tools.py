"""The tools the agent can call. Each one is plain, tested Python working on the business data,
described with a JSON schema so any OpenAI-compatible model can call it (function calling).

Every call is recorded in a trace, so a person can see exactly what the agent looked up.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .catalog import Catalog
from .data import Store
from .pricing import calculate_quote

TOOL_SPECS: list[dict] = [
    {"type": "function", "function": {
        "name": "lookup_customer",
        "description": "Find the sender in the CRM by email address. Returns tier, payment terms, city and credit "
                       "status, or found=false for a new customer.",
        "parameters": {"type": "object", "properties": {
            "email": {"type": "string", "description": "the sender's email address"}}, "required": ["email"]}}},
    {"type": "function", "function": {
        "name": "search_catalog",
        "description": "Search the product catalog with the customer's words for one product (without the "
                       "quantity). Returns up to 3 products with SKU, name, unit of sale, pack size, price and a "
                       "match score from 0 to 1. Use a product only if its score is at least 0.6; if several fit "
                       "equally well, the request is unclear.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "e.g. 'blue ballpoint pens' or 'A3 laminator'"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "check_stock",
        "description": "Stock for one SKU: units on hand, whether the quantity can be delivered now, and the "
                       "restock date for any shortfall.",
        "parameters": {"type": "object", "properties": {
            "sku": {"type": "string"}, "quantity": {"type": "integer", "minimum": 1}},
            "required": ["sku", "quantity"]}}},
    {"type": "function", "function": {
        "name": "calculate_quote",
        "description": "Price the order with the official price list: volume and customer discounts, delivery "
                       "fee and 5% VAT. Quantities are in the product's unit of sale (boxes, packs, each). Never "
                       "calculate prices yourself.",
        "parameters": {"type": "object", "properties": {
            "lines": {"type": "array", "items": {"type": "object", "properties": {
                "sku": {"type": "string"}, "quantity": {"type": "integer", "minimum": 1}},
                "required": ["sku", "quantity"]}},
            "customer_email": {"type": "string"},
            "delivery_city": {"type": "string", "description": "UAE city or emirate, e.g. Dubai, Abu Dhabi"}},
            "required": ["lines"]}}},
]


@dataclass
class ToolCall:
    step: int
    name: str
    arguments: dict
    result: dict
    seconds: float

    @property
    def summary(self) -> str:
        return summarise(self.name, self.arguments, self.result)


@dataclass
class Toolbox:
    store: Store
    today: date
    catalog: Catalog | None = None
    calls: list[ToolCall] = field(default_factory=list)

    def __post_init__(self):
        self.catalog = self.catalog or Catalog(self.store)

    # ------------------------------------------------------------------ tools
    def lookup_customer(self, email: str) -> dict:
        customer, match_note = self.store.resolve_customer(email)
        if customer:
            return {"found": True, "id": customer.id, "company": customer.company, "contact": customer.contact,
                    "tier": customer.tier, "payment_terms": customer.payment_terms, "city": customer.city,
                    "credit_available": float(customer.credit_limit - customer.open_balance),
                    **({"matched_by": "email domain"} if match_note else {})}
        return {"found": False, "note": "New customer: standard prices, payment before delivery."}

    def search_catalog(self, query: str) -> dict:
        matches = self.catalog.search(query, limit=3)
        return {"query": query, "results": [
            {"sku": m.product.sku, "name": m.product.name, "unit": m.product.unit,
             "pack_size": m.product.pack_size, "item": m.product.item, "price_aed": float(m.product.price),
             "score": m.score} for m in matches if m.score > 0]}

    def check_stock(self, sku: str, quantity: int) -> dict:
        if sku not in self.store.stock:
            return {"error": f"Unknown SKU '{sku}'. Use search_catalog to find the right SKU."}
        stock = self.store.stock[sku]
        quantity = int(quantity)
        result = {"sku": sku, "requested": quantity, "on_hand": stock.on_hand,
                  "can_deliver_now": min(quantity, stock.on_hand), "short": max(0, quantity - stock.on_hand)}
        if result["short"]:
            result["restock_date"] = stock.restock_date.isoformat() if stock.restock_date else None
            result["restock_qty"] = stock.restock_qty
        return result

    def calculate_quote(self, lines: list[dict], customer_email: str = "", delivery_city: str = "") -> dict:
        unknown = [line.get("sku") for line in lines if line.get("sku") not in self.store.products]
        if unknown:
            return {"error": f"Unknown SKU(s): {', '.join(map(str, unknown))}. Use search_catalog first."}
        customer, _ = self.store.resolve_customer(customer_email)
        city = (delivery_city or "").strip() or None
        quote = calculate_quote(self.store, [{"sku": l["sku"], "quantity": int(l["quantity"])} for l in lines],
                                customer, city, today=self.today, sender_email=customer_email)
        return {"lines": [{"sku": l.sku, "quantity": l.quantity, "unit_price": float(l.unit_price),
                           "volume_discount": f"{l.volume_rate:.0%}", "line_total": float(l.total)}
                          for l in quote.lines],
                "subtotal": float(quote.subtotal), "customer_discount": float(quote.tier_discount),
                "delivery_fee": float(quote.delivery_fee), "vat": float(quote.vat), "total": float(quote.total),
                "payment_terms": quote.payment_terms, "notes": quote.notes, "checks": quote.review_reasons}

    # ------------------------------------------------------------- dispatcher
    def call(self, name: str, arguments: str | dict | None, step: int = 0) -> dict:
        """Run a tool by name. Problems become an {"error": ...} result the model can read and correct."""
        started = time.perf_counter()
        args: dict = {}
        try:
            args = parse_arguments(arguments)
            if name not in TOOL_NAMES:
                result = {"error": f"Unknown tool '{name}'. Available: {', '.join(TOOL_NAMES)}."}
            else:
                result = getattr(self, name)(**args)
        except json.JSONDecodeError as exc:
            result = {"error": f"The arguments are not valid JSON ({exc.msg}). Send a JSON object."}
        except TypeError as exc:
            result = {"error": f"Bad arguments for {name}: {exc}"}
        except (ValueError, KeyError) as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}
        self.calls.append(ToolCall(step, name, args, result, round(time.perf_counter() - started, 4)))
        return result


TOOL_NAMES = [spec["function"]["name"] for spec in TOOL_SPECS]


def parse_arguments(arguments: str | dict | None) -> dict[str, Any]:
    """Models sometimes wrap arguments in code fences or send an empty string."""
    if isinstance(arguments, dict):
        return arguments
    text = (arguments or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    if not text:
        return {}
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Tool arguments must be a JSON object.")
    return value


def summarise(name: str, args: dict, result: dict) -> str:
    """One readable line per tool call, for the trace shown in the app."""
    if "error" in result:
        return f"⚠ {result['error']}"
    if name == "lookup_customer":
        return (f"{result['company']} · {result['tier']} · {result['payment_terms']}" if result.get("found")
                else "New customer (not in the CRM)")
    if name == "search_catalog":
        top = result["results"][0] if result["results"] else None
        if not top:
            return "No matching product"
        if top["score"] < 0.6:
            return f"No match above 60% (closest: {top['name']}, {top['score']:.0%})"
        return f"{top['sku']} · {top['name']} (match {top['score']:.0%})"
    if name == "check_stock":
        if result["short"]:
            return f"{result['can_deliver_now']} available now, {result['short']} short until {result.get('restock_date')}"
        return f"In stock ({result['on_hand']} on hand)"
    if name == "calculate_quote":
        return f"Total AED {result['total']:,.2f} incl. VAT ({len(result['lines'])} lines)"
    return json.dumps(result)[:120]
