"""The agents: rules mode on every labelled email, and the LLM tool loop with scripted and fake models."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from conftest import TODAY

from quoteflow.agent import LLMAgent, RulesAgent, parse_final, to_understanding
from quoteflow.evaluate import evaluate
from quoteflow.llm import LLMClient, LLMError, ScriptedLLM
from quoteflow.tools import TOOL_NAMES, TOOL_SPECS, Toolbox

MARINA_FINAL = {
    "intent": "quote_request", "route_to": "sales",
    "lines": [{"sku": "PAP-A4-80", "quantity": 40, "requested_as": "40 boxes of A4 copy paper"},
              {"sku": "MRK-WB-12", "quantity": 10, "requested_as": "10 packs of whiteboard markers"},
              {"sku": "CHR-ERGO", "quantity": 6, "requested_as": "6 ergonomic mesh chairs"},
              {"sku": "PEN-BL-50", "quantity": 25, "requested_as": "25 boxes of blue ballpoint pens"}],
    "not_found": [], "unclear": [], "delivery_city": "Dubai", "requested_discount_percent": None, "deadline": None,
    "note": "Thanks Sara, happy to help with the restock. Here is our quotation."}


def marina_script():
    return [
        {"tool_calls": [{"name": "lookup_customer", "arguments": {"email": "sara.haddad@marinatech.example"}},
                        {"name": "search_catalog", "arguments": {"query": "A4 copy paper"}},
                        {"name": "search_catalog", "arguments": {"query": "whiteboard markers"}},
                        {"name": "search_catalog", "arguments": {"query": "ergonomic mesh chairs"}},
                        {"name": "search_catalog", "arguments": {"query": "blue ballpoint pens"}}]},
        {"tool_calls": [{"name": "check_stock", "arguments": {"sku": "CHR-ERGO", "quantity": 6}},
                        {"name": "calculate_quote", "arguments": {
                            "lines": [{"sku": "PAP-A4-80", "quantity": 40}, {"sku": "MRK-WB-12", "quantity": 10},
                                      {"sku": "CHR-ERGO", "quantity": 6}, {"sku": "PEN-BL-50", "quantity": 25}],
                            "customer_email": "sara.haddad@marinatech.example", "delivery_city": "Dubai"}}]},
        json.dumps(MARINA_FINAL),
    ]


# ------------------------------------------------------------------ rules mode
def test_rules_agent_on_all_labelled_emails(store):
    report = evaluate(RulesAgent(store, today=TODAY))
    s = report.summary
    assert s["emails"] == 14
    assert s["line F1"] == 1.0 and s["totals exact"] == 1.0 and s["next step accuracy"] == 1.0


def test_rules_agent_reply_and_trace(store, inbox):
    result = RulesAgent(store, today=TODAY).run(inbox["04_desert_bloom_new_shop"], "Q-2026-1004")
    assert result.status == "ready" and result.quote.total == Decimal("1315.65")
    assert [c.name for c in result.tool_calls][:2] == ["lookup_customer", "search_catalog"]
    assert result.tool_calls[-1].name == "calculate_quote"
    body = result.reply_body
    assert body.startswith("Dear Omar,") and "welcome to Falcon Office Supplies" in body
    assert "Yes, we deliver to Ajman: delivery for this order is AED 45.00." in body
    assert "We don't currently carry: 3D printers." in body and "A3 laminator: back in stock on 14 October." in body
    assert "Total: AED 1,315.65" in body and result.reply_subject.endswith("Quotation Q-2026-1004")


def test_other_emails_are_forwarded_without_a_quote(store, inbox):
    result = RulesAgent(store, today=TODAY).run(inbox["06_sharjah_invoice_question"])
    assert result.status == "forward" and result.status_label == "Forward to accounts"
    assert result.quote is None and "accounts team" in result.reply_body


# -------------------------------------------------------------------- LLM mode
def test_llm_agent_uses_tools_then_the_system_prices_the_quote(store, inbox):
    llm = ScriptedLLM(marina_script())
    result = LLMAgent(llm, store, today=TODAY).run(inbox["01_marina_tech_restock"], "Q-2026-1001")
    assert result.mode == "llm" and result.llm_calls == 3 and result.status == "ready"
    assert [c.name for c in result.tool_calls] == ["lookup_customer"] + ["search_catalog"] * 4 + \
        ["check_stock", "calculate_quote"]
    assert result.quote.total == Decimal("9041.34")
    assert "Thanks Sara, happy to help with the restock." in result.reply_body  # the model's note...
    assert "Total: AED 9,041.34" in result.reply_body                          # ...the system's numbers
    # tool results go back to the model with the matching call ids
    second_request = llm.calls[1]
    tool_messages = [m for m in second_request if m["role"] == "tool"]
    assert len(tool_messages) == 5 and tool_messages[0]["tool_call_id"] == second_request[2]["tool_calls"][0]["id"]
    assert json.loads(tool_messages[1]["content"])["results"][0]["sku"] == "PAP-A4-80"


def test_invented_products_are_dropped_and_flagged(store, inbox):
    final = dict(MARINA_FINAL, lines=MARINA_FINAL["lines"] + [{"sku": "GOLD-PEN-1", "quantity": 2}])
    result = LLMAgent(ScriptedLLM([json.dumps(final)]), store, today=TODAY).run(inbox["01_marina_tech_restock"])
    assert "GOLD-PEN-1" not in {l.sku for l in result.quote.lines}
    assert result.status == "review" and any("unknown product code 'GOLD-PEN-1'" in r for r in result.review_reasons)


def test_bad_tool_calls_come_back_as_errors_the_model_can_fix(store, inbox):
    llm = ScriptedLLM([{"tool_calls": [{"name": "delete_database", "arguments": {}},
                                       {"name": "check_stock", "arguments": "{not json"},
                                       {"name": "check_stock", "arguments": {"sku": "NOPE", "quantity": 1}},
                                       {"name": "search_catalog", "arguments": {"wrong": "x"}}]},
                       json.dumps(MARINA_FINAL)])
    result = LLMAgent(llm, store, today=TODAY).run(inbox["01_marina_tech_restock"])
    errors = [c.result["error"] for c in result.tool_calls]
    assert "Unknown tool 'delete_database'" in errors[0] and "not valid JSON" in errors[1]
    assert "Unknown SKU 'NOPE'" in errors[2] and "Bad arguments for search_catalog" in errors[3]
    assert result.status == "ready"  # the model's final answer was still fine


def test_model_that_never_answers_falls_back_to_rules(store, inbox):
    llm = ScriptedLLM(["Sure! Let me think about it.", "Still thinking..."])
    result = LLMAgent(llm, store, today=TODAY).run(inbox["07_saadiyat_mixed_items"])
    assert result.llm_calls == 2 and result.mode == "rules"
    assert any("rules engine prepared this draft" in w for w in result.warnings)
    assert result.quote.total == Decimal("1244.15")


def test_model_errors_fall_back_or_raise(store, inbox):
    class Broken:
        label, model = "Broken", "x"

        def chat_tools(self, messages, tools):
            raise LLMError("Rate limit reached")

    result = LLMAgent(Broken(), store, today=TODAY).run(inbox["02_alreem_dental_desks"])
    assert result.mode == "rules" and any("Rate limit reached" in w for w in result.warnings)
    with pytest.raises(LLMError):
        LLMAgent(Broken(), store, today=TODAY, fallback=False).run(inbox["02_alreem_dental_desks"])


def test_parse_final_tolerates_fences_and_chatter():
    text = 'Here you go:\n```json\n{"intent": "other", "lines": []}\n```'
    assert parse_final(text) == {"intent": "other", "lines": []}
    assert parse_final("no json here") is None and parse_final('{"lines": []}') is None


def test_model_answer_is_validated(store):
    u, warnings = to_understanding({"intent": "quote_request", "lines": [
        {"sku": "chr-vis", "quantity": "4"}, {"sku": "CHR-VIS", "quantity": 1}, {"sku": "PAP-A4-80", "quantity": 0}],
        "delivery_city": "saadiyat island", "requested_discount_percent": "10%", "deadline": "2026-10-15"}, store, TODAY)
    assert [(l.sku, l.quantity) for l in u.lines] == [("CHR-VIS", 5)]
    assert u.delivery_city == "Abu Dhabi" and u.requested_discount == Decimal("0.10")
    assert str(u.deadline) == "2026-10-15" and any("no valid quantity for PAP-A4-80" in w for w in warnings)


def test_tool_specs_match_the_toolbox(store):
    assert TOOL_NAMES == ["lookup_customer", "search_catalog", "check_stock", "calculate_quote"]
    toolbox = Toolbox(store, TODAY)
    for spec in TOOL_SPECS:
        assert callable(getattr(toolbox, spec["function"]["name"]))
        assert spec["function"]["parameters"]["type"] == "object"


# --------------------------------------------- the real client against a fake API
def tool_call(call_id, name, args):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def test_full_loop_over_http_with_function_calling(store, inbox, fake_api):
    fake_api.script = [
        {"role": "assistant", "content": None, "tool_calls": [
            tool_call("c1", "lookup_customer", {"email": "noura@saadiyat-arch.example"}),
            tool_call("c2", "search_catalog", {"query": "DL envelopes"})],
         "extra_content": {"google": {"thought_signature": "sig-1"}}},
        {"role": "assistant", "content": None, "tool_calls": [
            {**tool_call("", "check_stock", {"sku": "ENV-DL-500", "quantity": 2}),
             "extra_content": {"google": {"thought_signature": "sig-2"}}}]},
        {"role": "assistant", "content": json.dumps({
            "intent": "quote_request", "lines": [{"sku": "ENV-DL-500", "quantity": 2}], "delivery_city": "Abu Dhabi",
            "note": "Thanks Noura!"})},
    ]
    llm = LLMClient("gemini", api_key="test-key", base_url=fake_api.base_url, backoff=0.01)
    result = LLMAgent(llm, store, today=TODAY).run(inbox["07_saadiyat_mixed_items"], "Q-2026-1007")
    assert result.llm_calls == 3 and [c.name for c in result.tool_calls] == ["lookup_customer", "search_catalog",
                                                                              "check_stock"]
    assert [(l.sku, l.quantity) for l in result.quote.lines] == [("ENV-DL-500", 2)]
    first, second, third = (r["body"] for r in fake_api.requests)
    assert first["tools"][0]["function"]["name"] == "lookup_customer" and first["tool_choice"] == "auto"
    echoed = second["messages"][2]
    assert echoed["tool_calls"][0]["id"] == "c1" and echoed["extra_content"]["google"]["thought_signature"] == "sig-1"
    assert [m["tool_call_id"] for m in second["messages"] if m["role"] == "tool"] == ["c1", "c2"]
    echoed = third["messages"][-2]  # a call without an id gets one, used on both sides
    generated = echoed["tool_calls"][0]["id"]
    assert generated and third["messages"][-1]["tool_call_id"] == generated
    assert echoed["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "sig-2"


def test_http_errors_become_friendly_messages(fake_api):
    fake_api.status_queue = [(400, {"error": {"message": "API key not valid. Please pass a valid API key."}})]
    llm = LLMClient("gemini", api_key="bad", base_url=fake_api.base_url, backoff=0.01)
    with pytest.raises(LLMError, match="API key was rejected"):
        llm.chat_tools([{"role": "user", "content": "hi"}], TOOL_SPECS)
