"""Quote maths, checked against totals worked out by hand."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from conftest import TODAY

from quoteflow.pricing import calculate_quote, money, volume_rate


def quote(store, lines, email="", city=None, **kwargs):
    customer = store.customer_by_email(email)
    return calculate_quote(store, [{"sku": s, "quantity": q} for s, q in lines], customer, city, today=TODAY,
                           sender_email=email, **kwargs)


def test_gold_customer_order_with_free_delivery(store):
    q = quote(store, [("PAP-A4-80", 40), ("MRK-WB-12", 10), ("CHR-ERGO", 6), ("PEN-BL-50", 25)],
              "sara.haddad@marinatech.example", "Dubai")
    assert q.subtotal == Decimal("9064.00")
    assert q.tier == "Gold" and q.tier_discount == Decimal("453.20")
    assert q.delivery_fee == Decimal("0.00") and q.vat == Decimal("430.54") and q.total == Decimal("9041.34")
    assert q.valid_until == date(2026, 10, 20) and q.payment_terms == "Net 30" and not q.review_reasons


def test_volume_discounts_by_quantity(store):
    assert [volume_rate(store, n) for n in (1, 49, 50, 99, 100, 249, 250, 1000)] == \
        [Decimal(x) for x in ("0", "0", "0.05", "0.05", "0.08", "0.08", "0.12", "0.12")]
    q = quote(store, [("WTR-500-24", 300)], "lina@cornicheevents.example", "Abu Dhabi")
    line = q.lines[0]
    assert (line.gross, line.discount, line.total) == (Decimal("5400.00"), Decimal("648.00"), Decimal("4752.00"))


def test_new_customer_pays_delivery_below_threshold(store):
    q = quote(store, [("SHR-CC20", 1), ("LAM-A3", 1), ("WBD-120", 1), ("KBM-WL", 2)], "omar@desertbloom.example",
              "Ajman", sender_name="Omar Rahman")
    assert q.customer is None and q.tier == "Standard" and q.payment_terms == "Prepaid"
    assert q.delivery_fee == Decimal("45.00") and q.vat == Decimal("62.65") and q.total == Decimal("1315.65")
    assert q.contact == "Omar Rahman" and any("New customer" in n for n in q.notes)


def test_vat_rounds_half_up_to_the_fils(store):
    assert money(Decimal("59.245")) == Decimal("59.25") and money(Decimal("11.644")) == Decimal("11.64")
    q = quote(store, [("ENV-DL-500", 2), ("STP-HD", 6), ("CBL-HDMI-2", 10), ("TNR-26A", 2)],
              "noura@saadiyat-arch.example", "Abu Dhabi")
    assert q.vat == Decimal("59.25") and q.total == Decimal("1244.15")


def test_credit_limit_needs_review(store):
    q = quote(store, [("DSK-ELEC-160", 2), ("CHR-VIS", 4), ("SAN-5L", 3)], "khalid@alreemdental.example", "Abu Dhabi")
    assert q.total == Decimal("4767.60")
    assert q.needs_review and "above the AED 15,000.00 limit" in q.review_reasons[0]


def test_large_orders_need_review_and_discount_requests_are_compared(store):
    q = quote(store, [("PAP-A4-80", 500), ("NOTE-76-12", 120)], "arjun.mehta@palmlogistics.example", "Dubai",
              requested_discount=Decimal("0.15"))
    assert q.total == Decimal("44889.10")
    assert q.review_reasons == ["Large order: total above AED 25,000.00."]  # standard discounts already 16.1%
    assert any("already come to 16.1%" in n for n in q.notes)
    q = quote(store, [("CHR-VIS", 2)], "noura@saadiyat-arch.example", "Abu Dhabi", requested_discount=Decimal("0.2"))
    assert any("asks for 20% off" in r for r in q.review_reasons)


def test_backorders_and_deadlines(store):
    q = quote(store, [("WTR-500-24", 300)], "lina@cornicheevents.example", "Abu Dhabi", deadline=date(2026, 10, 15))
    line = q.lines[0]
    assert (line.available_now, line.backorder, line.restock_date) == (180, 120, date(2026, 10, 20))
    assert q.has_backorder and any("needs delivery by 15 October" in r for r in q.review_reasons)
    q = quote(store, [("WTR-500-24", 300)], "lina@cornicheevents.example", "Abu Dhabi", deadline=date(2026, 10, 25))
    assert not q.review_reasons  # the restock arrives in time


@pytest.mark.parametrize("city, fee, flagged", [("Dubai", "35.00", False), ("Fujairah", "60.00", False),
                                                (None, "60.00", True), ("Doha", "60.00", True)])
def test_delivery_fees(store, city, fee, flagged):
    q = quote(store, [("CBL-HDMI-2", 2)], "", city)
    assert q.delivery_fee == Decimal(fee)
    assert any("Delivery city unknown" in r or "No delivery fee set" in r for r in q.review_reasons) == flagged
