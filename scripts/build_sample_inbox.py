"""Write the demo inbox: seven customer emails to Falcon Office Supplies (a fictional company).

    python scripts/build_sample_inbox.py
"""

from __future__ import annotations

from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "inbox"
TO = "Falcon Office Supplies <sales@falconoffice.example>"
START = datetime(2026, 10, 6, 8, 12, tzinfo=timezone(timedelta(hours=4)))  # Gulf Standard Time

EMAILS = [
    ("01_marina_tech_restock.eml", "Sara Haddad <sara.haddad@marinatech.example>", "Quote for office restock", """\
Hi team,

Could you please send us a quotation for the following:

- 40 boxes of A4 copy paper
- 10 packs of whiteboard markers
- 6 ergonomic mesh chairs
- 25 boxes of blue ballpoint pens

Delivery to our office in Dubai Media City, please.

Thanks,
Sara Haddad
Office Manager, Marina Tech Hub
"""),
    ("02_alreem_dental_desks.eml", "Dr. Khalid Mansour <khalid@alreemdental.example>", "Price request - clinic furniture", """\
Hello,

Please quote 2 electric standing desks, 4 visitor chairs and 3 hand sanitizer refills (5L). We need delivery to our clinic in Abu Dhabi.

Regards,
Dr. Khalid Mansour
Al Reem Dental Clinic
"""),
    ("03_corniche_event_supplies.eml", "Lina Farouk <lina@cornicheevents.example>", "Event supplies - need by 15 October", """\
Hi,

For a corporate event in Abu Dhabi we need:
300 cases of bottled water (500 ml)
12 cartons of 8oz paper cups
30 cartons of tissues

Can you send a quote and confirm you can deliver by 15 October?

Lina Farouk
Corniche Events
"""),
    ("04_desert_bloom_new_shop.eml", "Omar Rahman <omar@desertbloom.example>", "Prices for our new shop", """\
Hello,

We are opening a new flower shop in Ajman next month. How much would it cost for one paper shredder, an A3 laminator, a magnetic whiteboard and two wireless keyboard and mouse sets? Also, do you sell 3D printers?

Do you deliver to Ajman?

Best regards,
Omar Rahman
Desert Bloom Flowers
"""),
    ("05_palm_logistics_discount.eml", "Arjun Mehta <arjun.mehta@palmlogistics.example>", "Bulk order - 15% discount?", """\
Hi,

We'd like to order 500 boxes of A4 paper and 120 packs of sticky notes for our branches. If you can give us 15% off the total we'll confirm today.

Thanks,
Arjun Mehta
Procurement, Palm Logistics
"""),
    ("06_sharjah_invoice_question.eml", "Mariam Al Suwaidi <mariam@sharjahlearning.example>", "Invoice INV-2291 charged twice", """\
Hi,

We were charged twice for invoice INV-2291 last month. Could someone from accounts check and refund the duplicate payment?

Thank you,
Mariam Al Suwaidi
Sharjah Learning Centre
"""),
    ("07_saadiyat_mixed_items.eml", "Noura Khalil <noura@saadiyat-arch.example>", "Stationery and IT accessories", """\
Dear Falcon team,

Pls send your best price for:
1,000 DL envelopes
half a dozen staplers
10 nos HDMI cables 2m
2 x HP 26A toner

Deliver to our studio on Saadiyat Island, Abu Dhabi.

Noura Khalil
Saadiyat Architects
"""),
]


# Extra emails used only by the evaluation (eval/emails), to test phrasings the demo inbox doesn't show.
EVAL_EMAILS = [
    ("08_sharjah_stationery.eml", "Mariam Al Suwaidi <mariam@sharjahlearning.example>", "Stationery order", """Hello,

We need 2 boxes of black pens, 3 packs of highlighters and a heavy duty stapler for our Sharjah office.

Thanks,
Mariam
"""),
    ("09_marina_colleague_typos.eml", "Ali Rashed <ali.rashed@marinatech.example>", "chairs + monitor arms", """Hi, pls quote 4 ergonmic chairs and 2 dual monitor arms, deliver to JLT Dubai.
Ali
"""),
    ("10_palm_html_list.eml", "Arjun Mehta <arjun.mehta@palmlogistics.example>", "Quote request", None),
    ("11_saadiyat_unclear_chairs.eml", "Noura Khalil <noura@saadiyat-arch.example>", "Chairs and desks", """Hi, can I get a price for 10 chairs and 5 desks? Delivery to Abu Dhabi.
Noura
"""),
    ("12_marina_broken_chair.eml", "Sara Haddad <sara.haddad@marinatech.example>", "Broken chair from last order", """Hello,

One of the chairs from order Q-2026-0988 arrived with a broken leg. Could you arrange a replacement?

Sara
"""),
    ("13_baylaw_pantry.eml", "Procurement <procurement@baylaw.example>", "Monthly pantry order", """Dear Sir/Madam,

We'd like to set up a monthly order: 6 kg of coffee beans, 3 boxes of coffee capsules and 20 cases of water.
Delivery to Business Bay, Dubai.

Kind regards,
Bay Law Partners
"""),
    ("14_corniche_number_words.eml", "Lina Farouk <lina@cornicheevents.example>", "Office files", """Hi again,

Need twenty lever arch files and fifty boxes of staples asap.

Lina
"""),
]
HTML_BODY = ("<html><body><p>Hi Falcon team,</p><p>Please quote:</p><ul><li>50 packs of AA batteries</li>"
             "<li>100 bin bags rolls</li></ul><p>Thanks,<br>Arjun</p></body></html>")


def write(folder: Path, emails: list, offset: int) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for i, (name, sender, subject, body) in enumerate(emails):
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = TO
        msg["Subject"] = subject
        msg["Date"] = format_datetime(START + timedelta(minutes=37 * (i + offset)))
        msg["Message-ID"] = f"<{name.removesuffix('.eml')}@{sender.split('@')[1].rstrip('>')}>"
        if body is None:  # an HTML-only email, as sent by many web mail clients
            msg.set_content(HTML_BODY, subtype="html", cte="7bit")
        else:
            msg.set_content(body, cte="7bit")
        (folder / name).write_bytes(bytes(msg))
        print(folder.name + "/" + name)


def main() -> None:
    write(OUT, EMAILS, 0)
    write(OUT.parent / "eval" / "emails", EVAL_EMAILS, len(EMAILS))


if __name__ == "__main__":
    main()
