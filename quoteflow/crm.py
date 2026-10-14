"""What happens after a person decides: a CRM log of every email and an outbox of sent replies."""

from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .compose import AgentResult, quote_pdf, reply_eml
from .data import Store

FIELDS = ["time", "email_id", "from", "company", "subject", "action", "quote_number", "total_aed", "status_by_agent",
          "review_reasons", "mode"]


@dataclass
class CrmEntry:
    time: str
    email_id: str
    sender: str
    company: str
    subject: str
    action: str             # "sent", "sent to review", "forwarded", "rejected"
    quote_number: str
    total_aed: str
    status_by_agent: str
    review_reasons: str
    mode: str


class QuoteNumbers:
    """Q-2026-1001, Q-2026-1002, ... (continues from the highest number already in the log)."""

    def __init__(self, year: int, start: int = 1001):
        self.year, self.next_value = year, start

    def peek(self) -> str:
        return f"Q-{self.year}-{self.next_value}"

    def next(self) -> str:
        number = self.peek()
        self.next_value += 1
        return number

    def use(self, result) -> None:
        """Move on to the next number only if the result actually produced a quote."""
        if result.quote is not None:
            self.next_value += 1


class CrmLog:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.entries: list[CrmEntry] = []

    def record(self, result: AgentResult, action: str) -> CrmEntry:
        entry = CrmEntry(datetime.now().strftime("%Y-%m-%d %H:%M"), result.email.id, result.email.sender_email,
                         result.customer.company if result.customer else "(new customer)", result.email.subject,
                         action, result.quote.number if result.quote else "",
                         f"{result.quote.total:.2f}" if result.quote else "", result.status_label,
                         " | ".join(result.review_reasons), result.mode)
        self.entries.append(entry)
        if self.path:
            new_file = not self.path.exists()
            with self.path.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                if new_file:
                    writer.writerow(FIELDS)
                writer.writerow(list(asdict(entry).values()))
        return entry

    def to_csv(self) -> bytes:
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(FIELDS)
        writer.writerows(list(asdict(e).values()) for e in self.entries)
        return out.getvalue().encode("utf-8-sig")


def save_draft(result: AgentResult, store: Store, folder: str | Path, body: str | None = None) -> list[Path]:
    """Write the reply (.eml, opens in Outlook or Gmail) and the PDF quote to a folder."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    name = result.quote.number if result.quote else f"reply-{result.email.id}"
    paths = [folder / f"{name}.eml"]
    paths[0].write_bytes(reply_eml(result, store, body))
    if result.quote:
        paths.append(folder / f"{name}.pdf")
        paths[1].write_bytes(quote_pdf(result.quote, store))
    return paths
