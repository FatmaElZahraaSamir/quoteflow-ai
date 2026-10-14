"""Understanding what the customer asked for: product search and quantity extraction."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from conftest import TODAY

from quoteflow.catalog import Catalog
from quoteflow.extract import find_deadline, find_discount, find_lines, normalise_numbers, understand


@pytest.fixture(scope="module")
def catalog(store):
    return Catalog(store)


@pytest.mark.parametrize("query, sku", [
    ("A4 copy paper", "PAP-A4-80"), ("whiteboard markers", "MRK-WB-12"), ("whiteboard", "WBD-120"),
    ("blue ballpoint pens", "PEN-BL-50"), ("black pens", "PEN-BK-50"), ("ergonmic chair", "CHR-ERGO"),
    ("staplers", "STP-HD"), ("staples", "STA-26-5000"), ("hand sanitizer refills (5L)", "SAN-5L"),
    ("bottled water (500 ml)", "WTR-500-24"), ("8oz paper cups", "CUP-8OZ-1000"), ("post-it notes", "NOTE-76-12"),
    ("wireless keyboard and mouse sets", "KBM-WL"), ("HP 26A toner", "TNR-26A"), ("sanitiser", "SAN-5L"),
])
def test_search_finds_the_right_product(catalog, query, sku):
    assert catalog.best(query).product.sku == sku


def test_unknown_and_ambiguous_requests(catalog):
    assert catalog.resolve("3D printers") == (None, [])
    match, options = catalog.resolve("chairs")
    assert match is None and {o.product.sku for o in options} == {"CHR-ERGO", "CHR-VIS"}


def lines_of(text, catalog):
    lines, not_found, unclear = find_lines(text, catalog)
    return [(l.sku, l.quantity) for l in lines], not_found, unclear


@pytest.mark.parametrize("text, expected", [
    ("- 40 boxes of A4 copy paper\n- 10 packs of whiteboard markers", [("PAP-A4-80", 40), ("MRK-WB-12", 10)]),
    ("Please quote 2 electric standing desks, 4 visitor chairs and 3 hand sanitizer refills (5L).",
     [("DSK-ELEC-160", 2), ("CHR-VIS", 4), ("SAN-5L", 3)]),
    ("one paper shredder, an A3 laminator, a magnetic whiteboard and two wireless keyboard and mouse sets?",
     [("SHR-CC20", 1), ("LAM-A3", 1), ("WBD-120", 1), ("KBM-WL", 2)]),
    ("half a dozen staplers\n10 nos HDMI cables 2m\n2 x HP 26A toner", [("STP-HD", 6), ("CBL-HDMI-2", 10),
                                                                     ("TNR-26A", 2)]),
    ("2x sticky notes", [("NOTE-76-12", 2)]),
    ("Need twenty lever arch files and fifty boxes of staples asap.", [("FIL-LEVER-A4", 20), ("STA-26-5000", 50)]),
    ("300 cases of bottled water (500 ml)", [("WTR-500-24", 300)]),
])
def test_quantities_and_products(catalog, text, expected):
    assert lines_of(text, catalog)[0] == expected


def test_items_are_converted_to_units_of_sale(catalog):
    lines, _, _ = find_lines("1,000 DL envelopes and 200 blue pens", catalog)
    assert [(l.sku, l.quantity, l.conversion) for l in lines] == [
        ("ENV-DL-500", 2, "1,000 envelopes = 2 boxes of 500"), ("PEN-BL-50", 4, "200 pens = 4 boxes of 50")]
    lines, _, _ = find_lines("6 kg of coffee beans and 10 litres of hand sanitizer", catalog)
    assert [(l.sku, l.quantity) for l in lines] == [("COF-BEAN-1", 6), ("SAN-5L", 2)]


def test_numbers_that_are_not_orders_are_ignored(catalog):
    text = ("Invoice INV-2291, 15% off, deliver by 15 October, call +971 50 123 4567, "
            "one of the chairs is broken, we are a new shop")
    assert lines_of(text, catalog) == ([], [], [])


def test_unknown_products_and_missing_details_are_reported(catalog):
    lines, not_found, unclear = find_lines("We need 5 desks and some chairs. Do you sell 3D printers?", catalog)
    assert [(l.sku, l.quantity) for l in lines] == [("DSK-140", 5)]
    assert not_found == ["3D printers"]
    assert unclear == [{"requested_as": "chairs", "options": ["CHR-ERGO", "CHR-VIS"],
                        "missing": "quantity and product"}]


def test_discount_deadline_and_numbers():
    assert find_discount("If you can give us 15% off the total") == Decimal("0.15")
    assert find_discount("can you offer a discount of 10 %?") == Decimal("0.10")
    assert find_discount("best price please") is None
    assert find_deadline("confirm you can deliver by 15 October?", TODAY) == date(2026, 10, 15)
    assert find_deadline("we need it by March 3rd", TODAY) == date(2027, 3, 3)  # next occurrence
    assert normalise_numbers("half a dozen staplers and one shredder, 1,000 envelopes") == \
        "6 staplers and 1 shredder, 1000 envelopes"


@pytest.mark.parametrize("subject, body, intent, route", [
    ("Invoice INV-2291 charged twice", "We were charged twice, please refund.", "other", "accounts"),
    ("Broken chair", "One of the chairs from our last order arrived broken.", "other", "customer service"),
    ("Quote", "Please quote 3 visitor chairs.", "quote_request", "sales"),
])
def test_intent_and_routing(catalog, subject, body, intent, route):
    u = understand(subject, body, catalog, TODAY)
    assert (u.intent, u.route_to) == (intent, route)


def test_delivery_city_from_neighbourhood_names(store):
    assert store.find_city("Deliver to our studio on Saadiyat Island, Abu Dhabi.") == "Abu Dhabi"
    assert store.find_city("office in Dubai Media City") == "Dubai"
    assert store.find_city("deliver to JLT") == "Dubai"
    assert store.find_city("no city here") is None
