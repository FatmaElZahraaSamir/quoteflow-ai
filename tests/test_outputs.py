"""What leaves the system: PDF quotes, reply emails, the CRM log and the command line."""

from __future__ import annotations

import csv
import io
from email import policy
from email.parser import BytesParser

import pytest
from conftest import ROOT, TODAY

from quoteflow.__main__ import main
from quoteflow.agent import RulesAgent
from quoteflow.compose import quote_pdf, reply_eml
from quoteflow.crm import CrmLog, QuoteNumbers, save_draft
from quoteflow.emails import from_text, parse_eml


@pytest.fixture(scope="module")
def results(store, inbox):
    agent = RulesAgent(store, today=TODAY)
    return {key: agent.run(email, f"Q-2026-{1001 + i}") for i, (key, email) in enumerate(sorted(inbox.items()))}


def test_pdf_quote_contains_the_lines_and_totals(store, results):
    from pypdf import PdfReader

    result = results["07_saadiyat_mixed_items"]
    text = PdfReader(io.BytesIO(quote_pdf(result.quote, store))).pages[0].extract_text()
    for expected in ("QUOTATION", "Q-2026-1007", "Saadiyat Architects", "1,000 envelopes = 2 boxes of 500",
                     "Silver customer discount (3%)", "-35.10", "59.25", "1,244.15", "Valid until: 20 Oct 2026"):
        assert expected in text


def test_reply_email_threads_and_attaches_the_pdf(store, results):
    result = results["01_marina_tech_restock"]
    msg = BytesParser(policy=policy.default).parsebytes(reply_eml(result, store, body="Edited reply"))
    assert msg["To"] == "Sara Haddad <sara.haddad@marinatech.example>"
    assert msg["In-Reply-To"] == "<01_marina_tech_restock@marinatech.example>"
    assert msg["Subject"] == "Re: Quote for office restock · Quotation Q-2026-1001"
    assert msg.get_body(("plain",)).get_content().strip() == "Edited reply"
    attachment = next(msg.iter_attachments())
    assert attachment.get_filename() == "Q-2026-1001.pdf" and attachment.get_content().startswith(b"%PDF")


def test_save_draft_and_crm_log(tmp_path, store, results):
    paths = save_draft(results["02_alreem_dental_desks"], store, tmp_path)
    assert sorted(p.name for p in paths) == ["Q-2026-1002.eml", "Q-2026-1002.pdf"]
    log = CrmLog(tmp_path / "crm_log.csv")
    log.record(results["02_alreem_dental_desks"], "sent to review")
    log.record(results["06_sharjah_invoice_question"], "forwarded")
    rows = list(csv.DictReader((tmp_path / "crm_log.csv").open(encoding="utf-8")))
    assert [r["action"] for r in rows] == ["sent to review", "forwarded"]
    assert rows[0]["total_aed"] == "4767.60" and "Credit limit" in rows[0]["review_reasons"]
    assert rows[1]["quote_number"] == "" and rows[1]["status_by_agent"] == "Forward to accounts"
    assert log.to_csv().decode("utf-8-sig").count("\n") == 3


def test_quote_numbers_only_advance_when_a_quote_is_made(results):
    numbers = QuoteNumbers(2026)
    numbers.use(results["06_sharjah_invoice_question"])
    assert numbers.peek() == "Q-2026-1001"
    numbers.use(results["01_marina_tech_restock"])
    assert numbers.next() == "Q-2026-1002" and numbers.peek() == "Q-2026-1003"


def test_html_and_pasted_emails(store):
    raw = (ROOT / "eval" / "emails" / "10_palm_html_list.eml").read_bytes()
    email = parse_eml(raw, "html")
    assert "- 50 packs of AA batteries" in email.body and "<li>" not in email.body
    pasted = from_text("Please quote 3 visitor chairs, delivery to Dubai.", "Ali <ali@example.com>", "Chairs")
    result = RulesAgent(store, today=TODAY).run(pasted)
    assert pasted.sender_email == "ali@example.com" and result.quote.lines[0].sku == "CHR-VIS"


def test_cli_inbox_saves_drafts(tmp_path, capsys):
    assert main(["inbox", "--save", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Forward to accounts" in out and "44,889.10" in out
    assert len(list(tmp_path.glob("*.pdf"))) == 6 and (tmp_path / "crm_log.csv").exists()


def test_cli_run_and_eval(capsys):
    assert main(["run", str(ROOT / "inbox" / "05_palm_logistics_discount.eml")]) == 0
    out = capsys.readouterr().out
    assert "[Needs review]" in out and "16.1% off our list prices" in out
    assert main(["eval"]) == 0
    assert "line F1                100%" in capsys.readouterr().out


def test_cli_without_key(monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert main(["--provider", "gemini", "run", str(ROOT / "inbox" / "01_marina_tech_restock.eml")]) == 1
    assert "No API key" in capsys.readouterr().err
