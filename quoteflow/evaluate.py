"""Score an agent on hand-labelled emails (eval/expected.json).

* Intent      - quote request vs other email, and the right team for other emails
* Line items  - precision / recall / F1 on (product, quantity) pairs
* Quote total - exact to the fils (AED 0.01)
* Next step   - ready to send / needs review / forward, as a person would decide
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable

from .emails import Email, load_inbox

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class EvalReport:
    rows: list[dict]
    summary: dict

    def to_dataframe(self):
        import pandas as pd

        return pd.DataFrame(self.rows)


def load_expected(path: str | Path = ROOT / "eval" / "expected.json") -> dict[str, dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["emails"]


def evaluation_emails() -> list[Email]:
    return load_inbox(ROOT / "inbox") + load_inbox(ROOT / "eval" / "emails")


def evaluate(agent, emails: list[Email] | None = None, expected: dict | None = None,
             progress: Callable[[int, int, str], None] | None = None) -> EvalReport:
    emails = emails if emails is not None else evaluation_emails()
    expected = expected or load_expected()
    rows, tp, fp, fn = [], 0, 0, 0
    for i, email in enumerate(emails, start=1):
        if email.id not in expected:
            continue
        if progress:
            progress(i, len(emails), email.subject)
        want = expected[email.id]
        result = agent.run(email, quote_number=f"EVAL-{i:03d}")
        got = {line.sku: line.quantity for line in result.understanding.lines}
        want_lines = want.get("lines", {})
        pairs_got, pairs_want = set(got.items()), set(want_lines.items())
        tp += len(pairs_got & pairs_want)
        fp += len(pairs_got - pairs_want)
        fn += len(pairs_want - pairs_got)
        total = result.quote.total if result.quote else None
        total_ok = None if "total" not in want else total is not None and total == Decimal(str(want["total"])).quantize(
            Decimal("0.01"))
        intent_ok = result.understanding.intent == want["intent"] and (
            want["intent"] == "quote_request" or result.understanding.route_to == want.get("route_to"))
        rows.append({
            "email": email.id, "subject": email.subject,
            "intent ok": intent_ok,
            "lines ok": pairs_got == pairs_want,
            "missing": ", ".join(f"{s} x{q}" for s, q in sorted(pairs_want - pairs_got)),
            "extra": ", ".join(f"{s} x{q}" for s, q in sorted(pairs_got - pairs_want)),
            "total": float(total) if total is not None else None,
            "expected total": want.get("total"),
            "total ok": total_ok,
            "next step": result.status, "expected next step": want["status"],
            "next step ok": result.status == want["status"],
            "model calls": result.llm_calls, "tool calls": len(result.tool_calls), "seconds": result.seconds,
        })
    n = len(rows)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    with_total = [r for r in rows if r["total ok"] is not None]
    summary = {
        "emails": n,
        "intent accuracy": round(sum(r["intent ok"] for r in rows) / n, 3) if n else 0.0,
        "line precision": round(precision, 3),
        "line recall": round(recall, 3),
        "line F1": round(2 * precision * recall / (precision + recall), 3) if precision + recall else 0.0,
        "quotes fully correct": round(sum(r["lines ok"] for r in rows) / n, 3) if n else 0.0,
        "totals exact": round(sum(bool(r["total ok"]) for r in with_total) / len(with_total), 3) if with_total else 0.0,
        "next step accuracy": round(sum(r["next step ok"] for r in rows) / n, 3) if n else 0.0,
        "avg model calls": round(sum(r["model calls"] for r in rows) / n, 2) if n else 0.0,
        "avg seconds": round(sum(r["seconds"] for r in rows) / n, 3) if n else 0.0,
    }
    return EvalReport(rows, summary)
