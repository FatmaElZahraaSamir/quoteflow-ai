"""Business data the agent works with: catalog, stock, customers and pricing rules.

Plain CSV / JSON files, so a client can replace them with an export from their own
ERP or CRM without touching the code.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEMO_DATE = date(2026, 10, 6)  # the sample emails, stock and restock dates are set around this day


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    category: str
    unit: str          # unit of sale: box, pack, each...
    pack_size: int     # items per unit of sale (500 envelopes per box)
    item: str          # what one item is called ("envelope")
    price: Decimal     # price per unit of sale, excl. VAT
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Stock:
    sku: str
    dubai: int
    abu_dhabi: int
    restock_date: date | None = None
    restock_qty: int = 0

    @property
    def on_hand(self) -> int:
        return self.dubai + self.abu_dhabi


@dataclass(frozen=True)
class Customer:
    id: str
    company: str
    contact: str
    email: str
    tier: str
    payment_terms: str
    city: str
    credit_limit: Decimal
    open_balance: Decimal

    @property
    def first_name(self) -> str:
        parts = [p for p in self.contact.replace("Dr.", "").split() if p]
        return f"Dr. {parts[-1]}" if self.contact.startswith("Dr.") else (parts[0] if parts else "")


@dataclass
class PricingRules:
    company: str
    sales_email: str
    vat_rate: Decimal
    quote_valid_days: int
    volume_discounts: list[tuple[int, Decimal]]   # (min quantity, rate), highest first
    tier_discounts: dict[str, Decimal]
    free_delivery_over: Decimal
    delivery_fees: dict[str, Decimal]
    city_names: dict[str, list[str]]
    review_order_total_over: Decimal


@dataclass
class Store:
    products: dict[str, Product]
    stock: dict[str, Stock]
    customers: list[Customer]
    rules: PricingRules
    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls, folder: str | Path = DATA_DIR) -> "Store":
        folder = Path(folder)
        products = {}
        for row in _rows(folder / "catalog.csv"):
            products[row["sku"]] = Product(
                sku=row["sku"], name=row["name"], category=row["category"], unit=row["unit"],
                pack_size=int(row["pack_size"] or 1), item=row["item"], price=Decimal(row["price_aed"]),
                aliases=tuple(a.strip() for a in row.get("aliases", "").split(";") if a.strip()))
        stock = {}
        for row in _rows(folder / "stock.csv"):
            stock[row["sku"]] = Stock(row["sku"], int(row["dubai"] or 0), int(row["abu_dhabi"] or 0),
                                      date.fromisoformat(row["restock_date"]) if row.get("restock_date") else None,
                                      int(row.get("restock_qty") or 0))
        customers = [Customer(r["id"], r["company"], r["contact"], r["email"].lower(), r["tier"], r["payment_terms"],
                              r["city"], Decimal(r["credit_limit"] or 0), Decimal(r["open_balance"] or 0))
                     for r in _rows(folder / "customers.csv")]
        raw = json.loads((folder / "pricing.json").read_text(encoding="utf-8"))
        rules = PricingRules(
            company=raw["company"], sales_email=raw["sales_email"], vat_rate=Decimal(str(raw["vat_rate"])),
            quote_valid_days=int(raw["quote_valid_days"]),
            volume_discounts=sorted(((int(d["min_quantity"]), Decimal(str(d["rate"])))
                                     for d in raw["volume_discounts"]), reverse=True),
            tier_discounts={k: Decimal(str(v)) for k, v in raw["tier_discounts"].items()},
            free_delivery_over=Decimal(str(raw["delivery"]["free_over"])),
            delivery_fees={k: Decimal(str(v)) for k, v in raw["delivery"]["fees"].items()},
            city_names=raw["delivery"]["city_names"],
            review_order_total_over=Decimal(str(raw["review"]["order_total_over"])))
        missing = set(products) - set(stock)
        if missing:
            raise ValueError(f"No stock row for: {', '.join(sorted(missing))}")
        return cls(products, stock, customers, rules)

    # ------------------------------------------------------------------ lookups
    def customer_by_email(self, email: str) -> Customer | None:
        email = (email or "").strip().lower()
        return next((c for c in self.customers if c.email == email), None)

    def resolve_customer(self, email: str) -> tuple[Customer | None, str | None]:
        """Exact email match, else a single customer with the same company domain (with a note to check)."""
        customer = self.customer_by_email(email)
        if customer:
            return customer, None
        colleagues = self.customers_by_domain(email)
        if len(colleagues) == 1:
            return colleagues[0], (f"Check: {email} is not in the CRM; matched to {colleagues[0].company} "
                                   "by email domain.")
        return None, None

    def customers_by_domain(self, email: str) -> list[Customer]:
        domain = (email or "").strip().lower().rpartition("@")[2]
        return [c for c in self.customers if domain and c.email.endswith("@" + domain)]

    def find_city(self, text: str) -> str | None:
        """First delivery city mentioned in the text ('Saadiyat Island' -> 'Abu Dhabi')."""
        lowered = f" {text.lower()} "
        best: tuple[int, str] | None = None
        for city, names in self.rules.city_names.items():
            for name in names:
                position = lowered.find(f" {name}")
                if position >= 0 and (best is None or position < best[0]):
                    best = (position, city)
        return best[1] if best else None


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(handle)]
