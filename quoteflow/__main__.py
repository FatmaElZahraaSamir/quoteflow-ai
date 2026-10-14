"""Command line:

    python -m quoteflow run inbox/01_marina_tech_restock.eml           # one email, prints the draft reply
    python -m quoteflow inbox --save outbox                             # the whole inbox: drafts, PDFs, CRM log
    python -m quoteflow eval                                            # score on the 14 labelled emails

Add --provider gemini (key in the GEMINI_API_KEY environment variable) to let an LLM drive the tools;
without it the rules engine runs offline.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from .agent import LLMAgent, RulesAgent
from .crm import CrmLog, QuoteNumbers, save_draft
from .data import DATA_DIR, DEMO_DATE, Store
from .emails import load_inbox, parse_eml
from .evaluate import evaluate
from .llm import PROVIDERS, LLMClient, LLMError

ROOT = Path(__file__).resolve().parent.parent


def make_agent(args, store: Store):
    today = date.fromisoformat(args.today) if args.today else DEMO_DATE
    if args.provider:
        return LLMAgent(LLMClient(args.provider, model=args.model), store, today=today)
    return RulesAgent(store, today=today)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m quoteflow", description="AI quoting agent for sales inboxes.")
    parser.add_argument("--provider", choices=sorted(PROVIDERS), help="LLM provider; omit to use the rules engine")
    parser.add_argument("--model", help="model name (default depends on the provider)")
    parser.add_argument("--data", default=str(DATA_DIR), help="folder with catalog, stock, customers and pricing")
    parser.add_argument("--today", help=f"date used for quotes, YYYY-MM-DD (default: demo date {DEMO_DATE})")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="process one .eml file")
    run.add_argument("email")
    run.add_argument("--save", help="folder for the reply (.eml) and quote (.pdf)")
    inbox = sub.add_parser("inbox", help="process every .eml in a folder")
    inbox.add_argument("--folder", default=str(ROOT / "inbox"))
    inbox.add_argument("--save", help="folder for replies, PDFs and crm_log.csv")
    ev = sub.add_parser("eval", help="score the agent on the labelled emails")
    ev.add_argument("--out", help="save per-email results to this CSV file")
    args = parser.parse_args(argv)

    try:
        store = Store.load(args.data)
        agent = make_agent(args, store)
        numbers = QuoteNumbers(agent.today.year)
        if args.command == "run":
            path = Path(args.email)
            result = agent.run(parse_eml(path.read_bytes(), path.stem), numbers.peek())
            print(f"[{result.status_label}] {result.email.sender} · {result.email.subject}")
            for reason in result.review_reasons:
                print(f"  ! {reason}")
            for warning in result.warnings:
                print(f"  ~ {warning}")
            print(f"\nSubject: {result.reply_subject}\n\n{result.reply_body}")
            if args.save:
                for saved in save_draft(result, store, args.save):
                    print(f"Saved {saved}")
        elif args.command == "inbox":
            log = CrmLog(Path(args.save) / "crm_log.csv" if args.save else None)
            print(f"{'Email':32} {'Next step':26} {'Quote':12} {'Total (AED)':>12}")
            for email in load_inbox(args.folder):
                result = agent.run(email, numbers.peek())
                numbers.use(result)
                total = f"{result.quote.total:,.2f}" if result.quote else "-"
                print(f"{email.id[:32]:32} {result.status_label[:26]:26} "
                      f"{result.quote.number if result.quote else '-':12} {total:>12}")
                if args.save:
                    save_draft(result, store, args.save)
                    log.record(result, "drafted")
            if args.save:
                print(f"Drafts, PDFs and crm_log.csv saved in {args.save}")
        else:
            report = evaluate(agent, progress=lambda i, n, s: print(f"\r{i}/{n} {s[:50]:<50}", end="",
                                                                    file=sys.stderr))
            print(file=sys.stderr)
            for key, value in report.summary.items():
                shown = f"{value:.0%}" if isinstance(value, float) and value <= 1 and "avg" not in key else value
                print(f"{key:<22} {shown}")
            if args.out:
                report.to_dataframe().to_csv(args.out, index=False, encoding="utf-8-sig")
                print(f"Saved {args.out}")
    except (LLMError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
