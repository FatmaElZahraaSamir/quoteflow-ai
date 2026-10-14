"""Read customer emails (.eml files or pasted text)."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path


@dataclass
class Email:
    id: str
    sender_name: str
    sender_email: str
    subject: str
    body: str
    received: datetime | None = None
    message_id: str = ""

    @property
    def sender(self) -> str:
        return f"{self.sender_name} <{self.sender_email}>" if self.sender_name else self.sender_email


def parse_eml(raw: bytes, email_id: str) -> Email:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    name, address = parseaddr(str(msg.get("From", "")))
    part = msg.get_body(preferencelist=("plain", "html"))
    body = part.get_content() if part is not None else ""
    if part is not None and part.get_content_type() == "text/html":
        body = html_to_text(body)
    try:
        received = parsedate_to_datetime(str(msg["Date"])) if msg["Date"] else None
    except (TypeError, ValueError):
        received = None
    return Email(email_id, name.strip(), address.strip().lower(), str(msg.get("Subject", "")).strip(),
                 body.replace("\r\n", "\n").strip(), received, str(msg.get("Message-ID", "")).strip())


def html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style).*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", markup)
    markup = re.sub(r"(?i)<li[^>]*>", "- ", markup)
    return html.unescape(re.sub(r"<[^>]+>", "", markup))


def load_inbox(folder: str | Path) -> list[Email]:
    return [parse_eml(path.read_bytes(), path.stem) for path in sorted(Path(folder).glob("*.eml"))]


def from_text(body: str, sender: str = "", subject: str = "", email_id: str = "pasted") -> Email:
    """An email typed or pasted into the app. `sender` may be 'Name <address>' or just an address."""
    name, address = parseaddr(sender)
    return Email(email_id, name.strip(), address.strip().lower(), subject.strip() or "(no subject)", body.strip(),
                 datetime.now())
