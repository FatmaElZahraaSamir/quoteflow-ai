"""Turn what the agent understood into the things a salesperson needs: a priced quote, a reply
email ready to send, a PDF and the reasons a manager should look before it goes out.

Prices, totals and dates in the reply always come from pricing.py, never from the LLM.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid

from .data import Customer, Store
from .emails import Email
from .extract import Understanding
from .pricing import Quote, calculate_quote, fmt
from .tools import ToolCall

STATUS_LABELS = {"ready": "Ready to send", "review": "Needs review", "forward": "Forward to a colleague"}


@dataclass
class AgentResult:
    email: Email
    mode: str                          # "rules" or "llm"
    understanding: Understanding
    customer: Customer | None
    quote: Quote | None
    status: str                        # "ready" | "review" | "forward"
    review_reasons: list[str]
    reply_subject: str
    reply_body: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    llm_calls: int = 0
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def status_label(self) -> str:
        if self.status == "forward":
            return f"Forward to {self.understanding.route_to}"
        return STATUS_LABELS[self.status]


def build_result(email: Email, understanding: Understanding, store: Store, *, mode: str, quote_number: str,
                 today: date, note: str | None = None, tool_calls: list[ToolCall] | None = None,
                 warnings: list[str] | None = None) -> AgentResult:
    customer, match_note = store.resolve_customer(email.sender_email)
    warnings = list(warnings or []) + ([match_note] if match_note else [])
    quote = None
    if understanding.intent == "quote_request" and understanding.lines:
        quote = calculate_quote(
            store, [{"sku": l.sku, "quantity": l.quantity, "requested_as": l.requested_as,
                     "conversion": l.conversion} for l in understanding.lines],
            customer, understanding.delivery_city, number=quote_number, today=today,
            sender_email=email.sender_email, sender_name=email.sender_name,
            requested_discount=understanding.requested_discount, deadline=understanding.deadline)
    reasons = list(quote.review_reasons) if quote else []
    reasons += [w for w in warnings if w.startswith("Check:")]
    if understanding.intent != "quote_request":
        status = "forward"
    else:
        status = "review" if reasons else "ready"
    note = (note or "").strip() or default_note(email, understanding, quote, customer, store)
    subject = f"Re: {email.subject}" + (f" · Quotation {quote.number}" if quote else "")
    body = reply_text(email, understanding, quote, customer, store, note)
    return AgentResult(email, mode, understanding, customer, quote, status, reasons, subject, body,
                       list(tool_calls or []), warnings=warnings)


# ------------------------------------------------------------------ reply text
def greeting_name(email: Email, customer: Customer | None) -> str:
    if customer and customer.email == email.sender_email:
        return customer.first_name
    name = re.sub(r"^(mr|mrs|ms|dr)\.?\s+", "", email.sender_name, flags=re.I).split()
    return name[0] if name else "there"


def default_note(email: Email, u: Understanding, quote: Quote | None, customer: Customer | None,
                 store: Store) -> str:
    """The personal part of the reply when no LLM is used."""
    if u.intent != "quote_request":
        team = {"accounts": "accounts", "customer service": "customer service"}.get(u.route_to, "sales")
        return (f"Thank you for your email. I have passed it to our {team} team, who will get back to you "
                "within one business day.")
    parts = ["Thank you for getting in touch, and welcome to " + store.rules.company + "." if customer is None
             else "Thank you for your request."]
    if customer and customer.email != email.sender_email:
        parts[0] = f"Thank you for your request on behalf of {customer.company}."
    if quote:
        parts.append("Please find our quotation below." if not u.not_found else
                     "Please find our quotation below for the items we carry.")
        asked_delivery = re.search(r"deliver(?:y)? to ([A-Z][\w ]+)\?", email.body)
        if asked_delivery and quote.city:
            fee = "free" if not quote.delivery_fee else f"{fmt(quote.delivery_fee)}"
            parts.append(f"Yes, we deliver to {quote.city}: delivery for this order is {fee}.")
        if u.requested_discount and any("discount" in reason for reason in quote.review_reasons):
            parts.append(f"I have passed your request for {u.requested_discount:.0%} off to our sales manager, "
                         "who will come back to you on the final price shortly.")
        elif u.requested_discount and quote.lines:
            given = 1 - (quote.subtotal - quote.tier_discount) / sum(l.gross for l in quote.lines)
            parts.append(f"Good news on price: with your volume and {quote.tier} discounts this order already "
                         f"comes to {given:.1%} off our list prices, more than the {u.requested_discount:.0%} "
                         "you asked for.")
    elif u.unclear or u.not_found:
        parts.append("Before I can prepare a quotation, could you help me with the points below?")
    return " ".join(parts)


def reply_text(email: Email, u: Understanding, quote: Quote | None, customer: Customer | None, store: Store,
               note: str) -> str:
    rules = store.rules
    out = [f"Dear {greeting_name(email, customer)},", "", note.strip(), ""]
    if quote:
        out.append(f"Quotation {quote.number} (valid until {quote.valid_until:%d %B %Y})")
        out.append("")
        for line in quote.lines:
            discount = f", less {line.volume_rate:.0%} volume discount" if line.volume_rate else ""
            out.append(f"• {line.quantity} x {line.name} ({line.sku}) at {fmt(line.unit_price)}{discount}: "
                       f"{fmt(line.total)}")
            if line.conversion:
                out.append(f"  ({line.conversion})")
        out.append("")
        out.append(f"Subtotal: {fmt(quote.subtotal)}")
        if quote.tier_discount:
            out.append(f"{quote.tier} customer discount ({quote.tier_rate:.0%}): -{fmt(quote.tier_discount)}")
        city = f" to {quote.city}" if quote.city else ""
        out.append(f"Delivery{city}: " + ("free" if not quote.delivery_fee else fmt(quote.delivery_fee)))
        out.append(f"VAT {quote.vat_rate:.0%}: {fmt(quote.vat)}")
        out.append(f"Total: {fmt(quote.total)}")
        out.append("")
        backorders = [l for l in quote.lines if l.backorder]
        if backorders:
            out.append("Availability:")
            for line in backorders:
                when = f"{line.restock_date:%d %B}" if line.restock_date else "a date we will confirm"
                if line.available_now:
                    out.append(f"• {line.name}: {line.available_now} can be delivered now and the remaining "
                               f"{line.backorder} on {when}.")
                else:
                    out.append(f"• {line.name}: back in stock on {when}.")
            if u.deadline and any(l.restock_date is None or l.restock_date > u.deadline for l in backorders):
                out.append(f"  We will call you to agree the best option for your {u.deadline:%d %B} deadline.")
        else:
            out.append("Availability: everything is in stock and can be delivered within 1-2 business days.")
        out.append("")
    if u.not_found:
        out.append("We don't currently carry: " + ", ".join(u.not_found) + ".")
    for item in u.unclear:
        names = [store.products[s].name for s in item.get("options", []) if s in store.products]
        what = " or ".join(names) if names else "the product"
        if item.get("missing") == "quantity":
            out.append(f"• How many would you like of {what} (\"{item['requested_as']}\")?")
        else:
            out.append(f"• For \"{item['requested_as']}\", do you mean {what}?" +
                       (" And how many would you like?" if "quantity" in item.get("missing", "") else ""))
    if u.not_found or u.unclear:
        out.append("")
    if quote:
        out.append(f"Payment terms: {quote.payment_terms}.")
        out.append("The quotation is attached as a PDF. Simply reply to this email to confirm your order.")
        out.append("")
    out += ["Kind regards,", "Sales team", rules.company, rules.sales_email]
    return "\n".join(out)


# ------------------------------------------------------------------------- PDF
def quote_pdf(quote: Quote, store: Store) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    accent = colors.HexColor("#1d4e89")
    base = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=base["BodyText"], fontSize=9.5, leading=13)
    small = ParagraphStyle("small", parent=body, fontSize=8, textColor=colors.HexColor("#5f5e5a"))
    right = ParagraphStyle("right", parent=body, alignment=TA_RIGHT)
    rules = store.rules
    buffer = io.BytesIO()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#898781"))
        canvas.drawString(18 * mm, 10 * mm, f"{rules.company} · fictional company created for a portfolio demo")
        canvas.drawRightString(192 * mm, 10 * mm, f"{quote.number} · page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=18 * mm, title=f"Quotation {quote.number}", author=rules.company)
    story = [
        Table([[Paragraph(f"<b>{rules.company}</b><br/>Al Quoz Industrial Area 3, Dubai, UAE<br/>{rules.sales_email}",
                          body),
                Paragraph(f"<font size=18 color='#1d4e89'><b>QUOTATION</b></font><br/>{quote.number}<br/>"
                          f"Date: {quote.issued:%d %b %Y}<br/>Valid until: {quote.valid_until:%d %b %Y}", right)]],
              colWidths=[100 * mm, 74 * mm], style=[("VALIGN", (0, 0), (-1, -1), "TOP")]),
        Spacer(1, 8 * mm),
    ]
    bill_to = "<br/>".join(x for x in [f"<b>{quote.company}</b>" if quote.company else "", quote.contact,
                                         quote.email] if x)
    story.append(Table([[Paragraph(f"<font color='#5f5e5a'>Quotation for</font><br/>{bill_to}", body),
                         Paragraph(f"<font color='#5f5e5a'>Delivery to</font><br/>{quote.city or 'To be confirmed'}"
                                   f"<br/><font color='#5f5e5a'>Payment terms</font><br/>{quote.payment_terms}", body)]],
                       colWidths=[100 * mm, 74 * mm], style=[("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(Spacer(1, 6 * mm))

    head = ["#", "Description", "Qty", "Unit price", "Disc.", "Amount (AED)"]
    rows = [[Paragraph(f"<b>{h}</b>", right if i >= 2 else body) for i, h in enumerate(head)]]
    for i, line in enumerate(quote.lines, start=1):
        rows.append([Paragraph(str(i), body),
                     Paragraph(f"{line.name}<br/><font size=7.5 color='#5f5e5a'>{line.sku} · per {line.unit}"
                               f"{' · ' + line.conversion if line.conversion else ''}</font>", body),
                     Paragraph(f"{line.quantity:,}", right), Paragraph(f"{line.unit_price:,.2f}", right),
                     Paragraph(f"{line.volume_rate:.0%}" if line.volume_rate else "–", right),
                     Paragraph(f"{line.total:,.2f}", right)])
    table = Table(rows, colWidths=[8 * mm, 86 * mm, 14 * mm, 24 * mm, 14 * mm, 28 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, accent), ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#e1e0d9")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [table, Spacer(1, 4 * mm)]

    totals = [("Subtotal", f"{quote.subtotal:,.2f}")]
    if quote.tier_discount:
        totals.append((f"{quote.tier} customer discount ({quote.tier_rate:.0%})", f"-{quote.tier_discount:,.2f}"))
    totals.append((f"Delivery{' to ' + quote.city if quote.city else ''}",
                   "Free" if not quote.delivery_fee else f"{quote.delivery_fee:,.2f}"))
    totals.append((f"VAT {quote.vat_rate:.0%}", f"{quote.vat:,.2f}"))
    totals.append(("<b>Total (AED)</b>", f"<b>{quote.total:,.2f}</b>"))
    totals_table = Table([[Paragraph(label, right), Paragraph(value, right)] for label, value in totals],
                         colWidths=[146 * mm, 28 * mm])
    totals_table.setStyle(TableStyle([("LINEABOVE", (0, -1), (-1, -1), 0.8, accent),
                                      ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story += [totals_table, Spacer(1, 8 * mm)]

    notes = [n for n in quote.notes]
    notes.append(f"Prices in AED. Volume discounts: 5% from 50 units, 8% from 100, 12% from 250 of the same item. "
                 f"Free delivery for orders over {fmt(rules.free_delivery_over)}.")
    story.append(Paragraph("<b>Notes</b>", body))
    for note in notes:
        story.append(Paragraph(f"• {note}", small))
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


# ------------------------------------------------------------------------ email
def reply_eml(result: AgentResult, store: Store, body: str | None = None) -> bytes:
    """The reply as a standard .eml file (opens in Outlook / Gmail), with the PDF quote attached."""
    msg = EmailMessage()
    msg["From"] = formataddr((store.rules.company, store.rules.sales_email))
    msg["To"] = result.email.sender
    msg["Subject"] = result.reply_subject
    msg["Date"] = format_datetime(datetime.now().astimezone())
    msg["Message-ID"] = make_msgid(domain=store.rules.sales_email.split("@")[1])
    if result.email.message_id:
        msg["In-Reply-To"] = result.email.message_id
        msg["References"] = result.email.message_id
    msg.set_content(body or result.reply_body)
    if result.quote:
        msg.add_attachment(quote_pdf(result.quote, store), maintype="application", subtype="pdf",
                           filename=f"{result.quote.number}.pdf")
    return bytes(msg)
