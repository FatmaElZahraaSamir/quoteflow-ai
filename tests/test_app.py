"""The Streamlit app, driven headlessly with Streamlit's AppTest."""

from __future__ import annotations

import pytest
from conftest import ROOT


@pytest.fixture()
def app(monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception
    return at


def metric(at, label):
    return next(m.value for m in at.metric if m.label == label)


def button(at, label):
    return next(b for b in at.button if b.label == label)


def test_inbox_processes_every_email(app):
    assert metric(app, "Emails") == "7" and metric(app, "Processed") == "0"
    button(app, "Run the agent on all emails").click().run()
    assert not app.exception
    assert metric(app, "Processed") == "7" and metric(app, "Ready to send") == "3"
    assert metric(app, "Need a person") == "4" and metric(app, "Quoted (AED)") == "70,878"
    reply = next(t for t in app.text_area if t.label == "Reply").value
    assert reply.startswith("Dear Sara,") and "Total: AED 9,041.34" in reply


def test_approving_a_reply_logs_it(app):
    button(app, "Run the agent on this email").click().run()
    button(app, "Approve & send").click().run()
    assert not app.exception
    assert app.success[0].value == "Done: sent to customer."
    assert button(app, "Approve & send").disabled


def test_a_pasted_email_can_be_quoted(app):
    next(t for t in app.text_input if t.label == "From").set_value("Ali <ali@example.com>")
    next(t for t in app.text_input if t.label == "Subject").set_value("Chairs")
    next(t for t in app.text_area if t.label == "Email text").set_value(
        "Please quote 3 visitor chairs, delivery to Dubai.")
    button(app, "Add to inbox").click().run()
    assert metric(app, "Emails") == "8"
    button(app, "Run the agent on this email").click().run()
    reply = next(t for t in app.text_area if t.label == "Reply").value
    assert reply.startswith("Dear Ali,") and "3 x Stackable visitor chair" in reply


def test_missing_key_keeps_the_rules_engine(app):
    app.selectbox[0].set_value("Google Gemini (free tier)").run()
    assert not app.exception
    assert any("No API key" in w.value for w in app.warning)
    button(app, "Run the agent on this email").click().run()
    assert any("rules engine" in c.value for c in app.caption)
