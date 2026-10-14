"""Deterministic quote calculation. The LLM never does arithmetic: every amount comes from here.

Money uses Decimal with half-up rounding to fils (0.01 AED), like an accounting system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .data import Customer, Store

CENT = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt(amount: Decimal, currency: str = "AED") -> str:
    return f"{currency} {amount:,.2f}"


@dataclass
class QuoteLine:
    sku: str
    name: str
    quantity: int
    unit: str
    unit_price: Decimal
    volume_rate: Decimal
    gross: Decimal          # quantity x unit price
    discount: Decimal       # volume discount amount
    total: Decimal          # after volume discount
    available_now: int
    backorder: int
    restock_date: date | None
    requested_as: str = ""  # the customer's wording, e.g. "1,000 DL envelopes"
    conversion: str = ""    # e.g. "1,000 envelopes = 2 boxes of 500"


@dataclass
class Quote:
    number: str
    issued: date
    valid_until: date
    customer: Customer | None
    company: str
    contact: str
    email: str
    city: str | None
    lines: list[QuoteLine]
    subtotal: Decimal            # after volume discounts
    tier: str
    tier_rate: Decimal
    tier_discount: Decimal
    delivery_fee: Decimal
    vat_rate: Decimal
    vat: Decimal
    total: Decimal
    payment_terms: str
    notes: list[str] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return bool(self.review_reasons)

    @property
    def net(self) -> Decimal:
        """Goods after all discounts, plus delivery, before VAT."""
        return self.subtotal - self.tier_discount + self.delivery_fee

    @property
    def has_backorder(self) -> bool:
        return any(line.backorder for line in self.lines)


def volume_rate(store: Store, quantity: int) -> Decimal:
    return next((rate for min_qty, rate in store.rules.volume_discounts if quantity >= min_qty), Decimal("0"))


def calculate_quote(store: Store, lines: list[dict], customer: Customer | None, city: str | None,
                    number: str = "DRAFT", today: date | None = None, sender_email: str = "",
                    sender_name: str = "", requested_discount: Decimal | None = None,
                    deadline: date | None = None) -> Quote:
    """lines: [{"sku": ..., "quantity": ..., "requested_as": ..., "conversion": ...}]"""
    today = today or date.today()
    rules = store.rules
    quote_lines: list[QuoteLine] = []
    for line in lines:
        product = store.products[line["sku"]]
        quantity = int(line["quantity"])
        rate = volume_rate(store, quantity)
        gross = money(product.price * quantity)
        discount = money(gross * rate)
        stock = store.stock[product.sku]
        available = min(quantity, stock.on_hand)
        quote_lines.append(QuoteLine(product.sku, product.name, quantity, product.unit, product.price, rate, gross,
                                     discount, gross - discount, available, quantity - available,
                                     stock.restock_date if quantity > available else None,
                                     line.get("requested_as", ""), line.get("conversion", "")))

    subtotal = sum((line.total for line in quote_lines), Decimal("0"))
    tier = customer.tier if customer else "Standard"
    tier_rate = rules.tier_discounts.get(tier, Decimal("0"))
    tier_discount = money(subtotal * tier_rate)
    goods = subtotal - tier_discount
    delivery_city = city or (customer.city if customer else None)
    if not quote_lines or goods >= rules.free_delivery_over:
        delivery = Decimal("0.00")
    else:
        delivery = rules.delivery_fees.get(delivery_city or "", max(rules.delivery_fees.values()))
        delivery = money(delivery)
    vat = money((goods + delivery) * rules.vat_rate)
    total = goods + delivery + vat

    quote = Quote(
        number=number, issued=today, valid_until=today + timedelta(days=rules.quote_valid_days),
        customer=customer, company=customer.company if customer else "",
        contact=(customer.contact if customer and (not sender_email or customer.email == sender_email.lower())
                 else sender_name),
        email=sender_email or (customer.email if customer else ""),
        city=delivery_city, lines=quote_lines, subtotal=subtotal, tier=tier, tier_rate=tier_rate,
        tier_discount=tier_discount, delivery_fee=delivery, vat_rate=rules.vat_rate, vat=vat, total=total,
        payment_terms=customer.payment_terms if customer else "Prepaid")

    # ------------------------------------------------------------- business checks
    if customer is None:
        quote.notes.append("New customer: standard prices and payment before delivery.")
    if not delivery_city:
        quote.review_reasons.append("Delivery city unknown: the highest delivery fee was used.")
    elif delivery_city not in rules.delivery_fees:
        quote.review_reasons.append(f"No delivery fee set for {delivery_city}: check that we deliver there.")
    if customer and customer.payment_terms.lower().startswith("net"):
        exposure = customer.open_balance + total
        if exposure > customer.credit_limit:
            quote.review_reasons.append(
                f"Credit limit: open balance {fmt(customer.open_balance)} + this quote {fmt(total)} = "
                f"{fmt(exposure)}, above the {fmt(customer.credit_limit)} limit.")
    if total > rules.review_order_total_over:
        quote.review_reasons.append(f"Large order: total above {fmt(rules.review_order_total_over)}.")
    gross_total = sum((line.gross for line in quote_lines), Decimal("0"))
    if requested_discount and gross_total:
        given = Decimal(1) - goods / gross_total
        if requested_discount > given:
            quote.review_reasons.append(
                f"Customer asks for {requested_discount:.0%} off; the standard discounts come to {given:.1%}. "
                "A manager must approve any extra discount.")
        else:
            quote.notes.append(f"The standard discounts already come to {given:.1%}.")
    for line in quote_lines:
        if line.backorder:
            when = line.restock_date.strftime("%d %B %Y") if line.restock_date else "a date to be confirmed"
            note = (f"{line.name}: {line.available_now} in stock now, {line.backorder} more on {when}."
                    if line.available_now else f"{line.name}: out of stock, available on {when}.")
            quote.notes.append(note)
            if deadline and (line.restock_date is None or line.restock_date > deadline):
                quote.review_reasons.append(
                    f"Customer needs delivery by {deadline:%d %B}, but {line.name} is short until {when}.")
    return quote
