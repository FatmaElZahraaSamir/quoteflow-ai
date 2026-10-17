"""QuoteFlow AI - an agent that turns customer emails into priced quotes for one-click approval.

    streamlit run app.py
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from quoteflow.agent import LLMAgent, RulesAgent
from quoteflow.catalog import Catalog
from quoteflow.compose import AgentResult, quote_pdf, reply_eml
from quoteflow.crm import CrmLog, QuoteNumbers
from quoteflow.data import DEMO_DATE, Store
from quoteflow.emails import Email, from_text, load_inbox
from quoteflow.evaluate import evaluate, evaluation_emails
from quoteflow.llm import PROVIDERS, LLMClient, LLMError
from quoteflow.pricing import fmt

ROOT = Path(__file__).resolve().parent
UPWORK_URL = "https://www.upwork.com/freelancers/~01f79195ceec502c59"
ENGINES = {
    "Rules engine (demo, no API key)": None,
    "Google Gemini (free tier)": "gemini",
    "OpenAI": "openai",
    "Groq (free tier)": "groq",
    "Ollama (runs on your computer)": "ollama",
}
STATUS_ICON = {"new": "⚪", "ready": "🟢", "review": "🟠", "forward": "🔵", "done": "✅"}
BADGE = {"ready": ("Ready to send", "green", ":material/check_circle:"),
         "review": ("Needs review", "orange", ":material/warning:"),
         "forward": ("Forward", "blue", ":material/forward_to_inbox:")}

st.set_page_config(page_title="QuoteFlow AI · sales inbox agent", page_icon="📨", layout="wide")


def secret(name: str | None) -> str | None:
    if not name:
        return None
    try:
        value = st.secrets.get(name)
    except Exception:
        value = None
    return value or os.environ.get(name)


@st.cache_resource
def business() -> tuple[Store, Catalog]:
    store = Store.load()
    return store, Catalog(store)


store, catalog = business()
state = st.session_state
state.setdefault("results", {})
state.setdefault("actions", {})
state.setdefault("crm", CrmLog())
state.setdefault("numbers", QuoteNumbers(DEMO_DATE.year))
state.setdefault("outbox", [])
state.setdefault("pasted", [])

# -------------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("## 📨 QuoteFlow AI")
    st.caption("An AI agent that reads customer emails, checks the catalog, stock and CRM, prices the order and "
               "drafts the reply. A person approves before anything is sent.")
    st.markdown("**AI engine**")
    engine = st.selectbox("AI engine", list(ENGINES), index=1 if secret("GEMINI_API_KEY") else 0,
                          label_visibility="collapsed")
    provider = ENGINES[engine]
    llm, llm_error = None, None
    if provider:
        cfg = PROVIDERS[provider]
        stored_key = secret(cfg["env"])
        typed_key = ""
        if provider != "ollama":
            typed_key = st.text_input("API key", type="password",
                                      placeholder="Using the key from the app settings" if stored_key
                                      else "Paste your API key", help="Used only for this session and never saved.")
            if not (typed_key or stored_key):
                st.caption(f"Get a free key: [{cfg['key_url'].split('/')[2]}]({cfg['key_url']})")
        else:
            st.caption("Needs [Ollama](https://ollama.com/download) running on this computer.")
        model = st.text_input("Model", cfg["model"])
        try:
            llm = LLMClient(provider, api_key=typed_key.strip() or stored_key, model=model.strip() or None)
        except LLMError as exc:
            llm_error = str(exc)
    st.markdown("**Business data**")
    st.caption(f"{store.rules.company} (fictional): {len(store.products)} products, {len(store.customers)} "
               f"customers, delivery across the UAE. Demo date: {DEMO_DATE:%d %b %Y}.")
    st.divider()
    st.caption(f"Built by **Fatma El Zahraa**. Want an agent like this for your own inbox, catalog or CRM? "
               f"[Let's talk on Upwork]({UPWORK_URL}).")


def make_agent():
    if llm:
        return LLMAgent(llm, store, today=DEMO_DATE, catalog=catalog)
    return RulesAgent(store, today=DEMO_DATE, catalog=catalog)


def all_emails() -> list[Email]:
    return load_inbox(ROOT / "inbox") + state.pasted


def run_agent(email: Email) -> AgentResult:
    result = make_agent().run(email, state.numbers.peek())
    state.numbers.use(result)
    state.results[email.id] = result
    state.actions.pop(email.id, None)
    state.pop(f"body_{email.id}", None)
    return result


def status_of(email: Email) -> str:
    if email.id in state.actions:
        return "done"
    result = state.results.get(email.id)
    return result.status if result else "new"


def engine_badge() -> None:
    if llm:
        st.badge(f"{llm.label} · {llm.model}", icon=":material/auto_awesome:", color="violet")
    else:
        st.badge("Rules engine · demo mode", icon=":material/rule:", color="gray")
    if llm_error:
        st.warning(f"{llm_error} Using the rules engine until a key is added.")


# ---------------------------------------------------------------------- pages
def inbox_page() -> None:
    emails = all_emails()
    left, right = st.columns([3, 2], vertical_alignment="bottom")
    with left:
        st.markdown("### Sales inbox")
    with right:
        with st.container(horizontal=True, horizontal_alignment="right"):
            engine_badge()
    results = [state.results[e.id] for e in emails if e.id in state.results]
    quoted = sum((r.quote.total for r in results if r.quote), start=0)
    cols = st.columns(5)
    cols[0].metric("Emails", len(emails))
    cols[1].metric("Processed", len(results))
    cols[2].metric("Ready to send", sum(r.status == "ready" for r in results))
    cols[3].metric("Need a person", sum(r.status != "ready" for r in results))
    cols[4].metric("Quoted (AED)", f"{quoted:,.0f}")

    with st.container(horizontal=True, gap="small"):
        if st.button("Run the agent on all emails", type="primary", icon=":material/bolt:"):
            bar = st.progress(0.0)
            for i, email in enumerate(emails, start=1):
                if email.id not in state.results:
                    bar.progress(i / len(emails), text=f"{i}/{len(emails)} · {email.subject}")
                    run_agent(email)
            bar.empty()
            st.rerun()
        with st.popover("Paste a new email", icon=":material/add:"):
            with st.form("paste", clear_on_submit=True):
                sender = st.text_input("From", placeholder="Name <name@company.com>")
                subject = st.text_input("Subject")
                body = st.text_area("Email text", height=180,
                                    placeholder="Hi, please quote 20 boxes of A4 paper and 5 visitor chairs...")
                if st.form_submit_button("Add to inbox") and body.strip():
                    email = from_text(body, sender or "customer@example.com", subject,
                                      email_id=f"pasted_{len(state.pasted) + 1:02d}")
                    state.pasted.append(email)
                    state.selected = email.id
                    # A new table key makes the inbox open on the pasted email. Newer Streamlit
                    # versions keep a keyed table's old selection, so changing the default alone
                    # would leave the first email selected.
                    state.table_version = state.get("table_version", 0) + 1
                    st.rerun()

    table = pd.DataFrame([{
        "": STATUS_ICON[status_of(e)],
        "From": (store.customer_by_email(e.sender_email).company if store.customer_by_email(e.sender_email)
                 else e.sender_name or e.sender_email),
        "Subject": e.subject,
    } for e in emails])
    list_col, detail_col = st.columns([1, 1.2], gap="medium")
    with list_col:
        ids = [e.id for e in emails]
        selected_id = state.get("selected") if state.get("selected") in ids else ids[0]
        event = st.dataframe(table, hide_index=True, width="stretch", on_select="rerun",
                             selection_mode="single-row", key=f"inbox_table_{state.get('table_version', 0)}",
                             selection_default={"selection": {"rows": [ids.index(selected_id)], "columns": [],
                                                              "cells": []}},
                             column_config={"": st.column_config.TextColumn(width=36),
                                            "From": st.column_config.TextColumn(width="medium"),
                                            "Subject": st.column_config.TextColumn(width="large")})
        rows = event.selection.rows if event and event.selection else []
        if rows:
            selected_id = ids[rows[0]]
            state.selected = selected_id
        st.caption("⚪ new · 🟢 ready to send · 🟠 needs review · 🔵 forward · ✅ done")
        with st.container(border=True):
            st.markdown("**What the agent does with each email**")
            st.markdown(
                "1. Reads the request and finds the customer in the CRM\n"
                "2. Matches each product in the catalog, even with typos, and converts units (1,000 envelopes = 2 boxes)\n"
                "3. Checks stock and restock dates\n"
                "4. Prices the order with your rules: volume and customer discounts, delivery, 5% VAT. "
                "The AI never does the maths\n"
                "5. Checks credit limit, big orders, discount requests and deadlines\n"
                "6. Drafts the reply and the PDF quote. You approve, edit or forward")
    with detail_col:
        email_detail(next(e for e in emails if e.id == selected_id))


def email_detail(email: Email) -> None:
    with st.container(border=True, height=250):
        st.markdown(f"**{email.subject}**")
        received = f" · {email.received:%d %b %Y, %H:%M}" if email.received else ""
        st.caption(f"From {email.sender}{received}")
        st.markdown(email.body.replace("$", "\\$").replace("\n", "  \n"))
    result: AgentResult | None = state.results.get(email.id)
    if result is None:
        if st.button("Run the agent on this email", type="primary", icon=":material/play_arrow:"):
            with st.spinner("Reading the email, checking catalog, stock and CRM…"):
                run_agent(email)
            st.rerun()
        return

    label, color, icon = BADGE[result.status]
    if result.status == "forward":
        label = result.status_label
    with st.container(horizontal=True, vertical_alignment="center"):
        st.badge(label, icon=icon, color=color)
        calls = f" · {result.llm_calls} model calls" if result.llm_calls else ""
        st.caption(f"{len(result.tool_calls)} tool calls{calls} · {result.seconds * 1000:,.0f} ms · "
                   f"{'AI agent' if result.mode == 'llm' else 'rules engine'}")
    for warning in result.warnings:
        if not warning.startswith("Check:"):
            st.info(warning)
    for reason in result.review_reasons:
        st.warning(reason, icon=":material/warning:")
    if email.id in state.actions:
        st.success(f"Done: {state.actions[email.id]}.", icon=":material/task_alt:")

    tabs = st.tabs(["Reply draft", "Quote", "Agent steps"])
    with tabs[0]:
        st.caption(f"To: {email.sender}  \nSubject: {result.reply_subject}")
        body = st.text_area("Reply", result.reply_body, height=420, key=f"body_{email.id}",
                            label_visibility="collapsed")
        if result.quote:
            st.caption(f"📎 {result.quote.number}.pdf attached")
        with st.container(horizontal=True, gap="small"):
            done = email.id in state.actions
            if result.status == "forward":
                team = result.understanding.route_to
                if st.button(f"Forward to {team}", type="primary", disabled=done, icon=":material/forward:"):
                    decide(email, result, body, f"forwarded to {team}")
            else:
                if st.button("Approve & send", type="primary", disabled=done, icon=":material/send:"):
                    decide(email, result, body, "sent to customer")
                if result.status == "review" and st.button("Send to manager", disabled=done,
                                                           icon=":material/supervisor_account:"):
                    decide(email, result, body, "sent to manager for review")
            if st.button("Run again", icon=":material/refresh:"):
                run_agent(email)
                st.rerun()
            if result.quote:
                st.download_button("PDF", quote_pdf(result.quote, store), f"{result.quote.number}.pdf",
                                   "application/pdf", icon=":material/download:")
            st.download_button(".eml", reply_eml(result, store, body), f"reply-{email.id}.eml", "message/rfc822",
                               icon=":material/download:")
    with tabs[1]:
        quote_view(result)
    with tabs[2]:
        steps_view(result)


def decide(email: Email, result: AgentResult, body: str, action: str) -> None:
    state.actions[email.id] = action
    state.crm.record(result, action)
    if action == "sent to customer":
        name = result.quote.number if result.quote else f"reply-{email.id}"
        state.outbox.append({"name": name, "to": email.sender, "subject": result.reply_subject,
                             "time": datetime.now().strftime("%H:%M"), "eml": reply_eml(result, store, body),
                             "pdf": quote_pdf(result.quote, store) if result.quote else None})
    st.rerun()


def quote_view(result: AgentResult) -> None:
    quote = result.quote
    u = result.understanding
    if not quote:
        st.info("No quote for this email." if u.intent != "quote_request" else
                "Nothing to price yet: the reply asks the customer to clarify.")
    else:
        st.markdown(f"**{quote.number}** · {quote.company or quote.contact} · {quote.tier} · {quote.payment_terms} · "
                    f"valid until {quote.valid_until:%d %b %Y}")
        st.dataframe(pd.DataFrame([{
            "Item": l.name, "Qty": l.quantity, "Price": float(l.unit_price), "Amount": float(l.total),
            "Stock": "✓ in stock" if not l.backorder else
            (f"⚠ {l.backorder} on {l.restock_date:%d %b}" if l.restock_date else "⚠ out of stock"),
        } for l in quote.lines]), hide_index=True, width="stretch",
            column_config={"Item": st.column_config.TextColumn(width=200),
                           "Qty": st.column_config.NumberColumn(width=45, format="%d"),
                           "Price": st.column_config.NumberColumn(width=70, format="accounting"),
                           "Amount": st.column_config.NumberColumn(width=85, format="accounting"),
                           "Stock": st.column_config.TextColumn(width=105)})
        totals = [("Subtotal", f"{quote.subtotal:,.2f}")]
        if quote.tier_discount:
            totals.append((f"{quote.tier} discount ({quote.tier_rate:.0%})", f"-{quote.tier_discount:,.2f}"))
        totals.append((f"Delivery{' to ' + quote.city if quote.city else ''}",
                       "Free" if not quote.delivery_fee else f"{quote.delivery_fee:,.2f}"))
        totals.append((f"VAT {quote.vat_rate:.0%}", f"{quote.vat:,.2f}"))
        left, right = st.columns([1, 1], vertical_alignment="bottom")
        with left:
            st.metric("Total incl. VAT", fmt(quote.total))
        with right:
            st.dataframe(pd.DataFrame(totals, columns=["", "AED"]), hide_index=True, width="stretch",
                         column_config={"AED": st.column_config.TextColumn(width=110)})
    for line in u.lines:
        if line.conversion:
            st.caption(f"Converted: {line.requested_as} → {line.conversion}")
    if u.not_found:
        st.caption("Not in the catalog: " + ", ".join(u.not_found))
    for item in u.unclear:
        st.caption(f"Asked the customer about \"{item['requested_as']}\" (options: {', '.join(item['options'])})")


def steps_view(result: AgentResult) -> None:
    if not result.tool_calls:
        st.caption("No tools were needed: this email is not a quote request.")
        return
    st.caption("Every lookup the agent made, in order. Prices always come from calculate_quote, never from the AI.")
    for i, call in enumerate(result.tool_calls, start=1):
        args = ", ".join(f"{k}={json.dumps(v)}" for k, v in call.arguments.items() if k != "lines")
        if "lines" in call.arguments:
            args = f"{len(call.arguments['lines'])} lines" + (", " + args if args else "")
        with st.expander(f"{i}. `{call.name}({args})` → {call.summary}"):
            st.json(call.result, expanded=False)


def sent_page() -> None:
    st.markdown("### Sent & CRM log")
    entries = state.crm.entries
    cols = st.columns(4)
    sent = [e for e in entries if e.action == "sent to customer"]
    cols[0].metric("Quotes sent", sum(1 for e in sent if e.quote_number))
    cols[1].metric("Value sent (AED)", f"{sum(float(e.total_aed or 0) for e in sent):,.0f}")
    cols[2].metric("With a manager", sum(1 for e in entries if "manager" in e.action))
    cols[3].metric("Forwarded", sum(1 for e in entries if e.action.startswith("forwarded")))
    if not entries:
        st.info("Nothing here yet. Approve or forward an email in the inbox and it will be logged here.")
        return
    log = pd.DataFrame([vars(e) for e in entries])
    st.dataframe(log[["time", "company", "subject", "action", "quote_number", "total_aed", "status_by_agent"]],
                 hide_index=True, width="stretch",
                 column_config={"time": "Time", "company": "Company", "subject": "Subject", "action": "Action",
                                "quote_number": "Quote", "status_by_agent": "Agent suggested",
                                "total_aed": st.column_config.NumberColumn("Total (AED)", format="%.2f")})
    st.download_button("Download CRM log (CSV)", state.crm.to_csv(), "crm_log.csv", "text/csv",
                       icon=":material/download:")
    if state.outbox:
        st.markdown("#### Outbox")
        for item in reversed(state.outbox):
            with st.container(border=True, horizontal=True, vertical_alignment="center"):
                st.markdown(f"**{item['subject']}**  \nTo {item['to']} · {item['time']}")
                st.download_button(".eml", item["eml"], f"{item['name']}.eml", "message/rfc822",
                                   key=f"eml_{item['name']}", icon=":material/mail:")
                if item["pdf"]:
                    st.download_button("PDF", item["pdf"], f"{item['name']}.pdf", "application/pdf",
                                       key=f"pdf_{item['name']}", icon=":material/picture_as_pdf:")


def catalog_page() -> None:
    st.markdown("### Catalog, stock & pricing rules")
    st.caption("Plain CSV / JSON files in the data folder, so they can be swapped for an export from any ERP or CRM.")
    category = st.pills("Category", ["All"] + sorted({p.category for p in store.products.values()}), default="All")
    rows = []
    for p in store.products.values():
        if category not in (None, "All") and p.category != category:
            continue
        s = store.stock[p.sku]
        rows.append({"SKU": p.sku, "Product": p.name, "Category": p.category, "Unit": p.unit,
                     "Price (AED)": float(p.price), "Dubai": s.dubai, "Abu Dhabi": s.abu_dhabi,
                     "Restock": f"{s.restock_date:%d %b} (+{s.restock_qty})" if s.restock_date else ""})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={"Price (AED)": st.column_config.NumberColumn(format="%.2f"),
                                "Product": st.column_config.TextColumn(width="large")})
    c1, c2, c3 = st.columns(3)
    rules = store.rules
    with c1:
        st.markdown("**Volume discounts** (same item)")
        st.dataframe(pd.DataFrame([{"From qty": q, "Discount": f"{r:.0%}"} for q, r in sorted(rules.volume_discounts)]),
                     hide_index=True, width="stretch")
    with c2:
        st.markdown("**Customer tiers**")
        st.dataframe(pd.DataFrame([{"Tier": t, "Discount": f"{r:.0%}"} for t, r in rules.tier_discounts.items()]),
                     hide_index=True, width="stretch")
    with c3:
        st.markdown(f"**Delivery** (free over {fmt(rules.free_delivery_over)})")
        st.dataframe(pd.DataFrame([{"City": c, "Fee (AED)": float(f)} for c, f in rules.delivery_fees.items()]),
                     hide_index=True, width="stretch")
    st.markdown("**Customers**")
    st.dataframe(pd.DataFrame([{"ID": c.id, "Company": c.company, "Contact": c.contact, "Email": c.email,
                                "Tier": c.tier, "Terms": c.payment_terms, "City": c.city,
                                "Credit limit": float(c.credit_limit), "Open balance": float(c.open_balance)}
                               for c in store.customers]), hide_index=True, width="stretch")
    st.caption(f"Checks before a quote goes out: credit limit (open balance + quote), orders over "
               f"{fmt(rules.review_order_total_over)}, discount requests above the standard discounts, stock that "
               "can't meet the customer's deadline, unknown delivery city, and customers matched only by email domain.")


def evaluation_page() -> None:
    st.markdown("### Evaluation on 14 labelled emails")
    st.caption("Each email has a hand-checked answer: the products and quantities, the total to the fils, and the "
               "right next step (send, review or forward). Seven are the demo inbox; seven more test other "
               "phrasings: HTML email, typos, number words, kilograms, a complaint that mentions a product.")
    agent = make_agent()
    if llm:
        st.info(f"Runs with {llm.label}: about 3 model calls per email (~40 in total). Free tiers have per-minute "
                "limits, so this can take a few minutes.")
    if st.button("Run the evaluation", type="primary", icon=":material/play_arrow:"):
        bar = st.progress(0.0)
        try:
            report = evaluate(agent, evaluation_emails(),
                              progress=lambda i, n, s: bar.progress((i - 1) / n, text=f"{i}/{n} · {s}"))
        except LLMError as exc:
            st.error(f"Evaluation stopped: {exc}")
            return
        bar.empty()
        state.eval_report = (report, agent.label if hasattr(agent, "label") else "")
    if "eval_report" not in state:
        return
    report, label = state.eval_report
    s = report.summary
    cols = st.columns(5)
    cols[0].metric("Intent & routing", f"{s['intent accuracy']:.0%}")
    cols[1].metric("Line items F1", f"{s['line F1']:.0%}", help=f"Precision {s['line precision']:.0%}, "
                   f"recall {s['line recall']:.0%} on (product, quantity) pairs.")
    cols[2].metric("Quotes fully correct", f"{s['quotes fully correct']:.0%}")
    cols[3].metric("Totals exact to the fils", f"{s['totals exact']:.0%}")
    cols[4].metric("Right next step", f"{s['next step accuracy']:.0%}")
    calls = f"{s['avg model calls']} model calls" if s["avg model calls"] else "no model calls"
    st.caption(f"{label} · {calls} and {s['avg seconds'] * 1000:,.0f} ms per email")
    if not s["avg model calls"]:
        st.caption("The rules engine was built and tuned on these emails, so 100% here shows that the pricing, "
                   "checks and routing work, not that rules understand every phrasing. Choose an AI engine in the "
                   "sidebar to test the LLM agent on the same emails.")
    df = report.to_dataframe()
    df.insert(0, "", (df["lines ok"] & df["next step ok"] & df["intent ok"] & (df["total ok"] != False))  # noqa: E712
              .map({True: "✅", False: "❌"}))
    st.dataframe(df[["", "email", "missing", "extra", "total", "expected total", "next step",
                     "expected next step", "tool calls"]], hide_index=True, width="stretch",
                 height=35 * (len(df) + 1) + 3,
                 column_config={"": st.column_config.TextColumn(width=36),
                                "total": st.column_config.NumberColumn(format="%.2f"),
                                "expected total": st.column_config.NumberColumn(format="%.2f")})


navigation = st.navigation([
    st.Page(inbox_page, title="Inbox", icon=":material/inbox:", default=True),
    st.Page(sent_page, title="Sent & CRM", icon=":material/send:"),
    st.Page(catalog_page, title="Catalog & rules", icon=":material/inventory_2:"),
    st.Page(evaluation_page, title="Evaluation", icon=":material/fact_check:"),
], position="top")
navigation.run()
