"""Acme Corp invoice processing.

    python main.py --invoice_path=data/invoices/invoice_1001.txt
    python main.py                     # every invoice in data/invoices
"""

import argparse
import json
import logging
from pathlib import Path

from invoices import db as store
from invoices.grok import from_env
from invoices.pipeline import process

LABELS = {"approved": "APPROVED", "rejected": "REJECTED", "needs_review": "NEEDS REVIEW", "error": "ERROR"}


def main():
    parser = argparse.ArgumentParser(description="Process vendor invoices for Acme Corp")
    parser.add_argument("--invoice_path", default="data/invoices", help="an invoice file or a folder of them")
    parser.add_argument("--offline", action="store_true", help="don't call Grok even if XAI_API_KEY is set")
    parser.add_argument("--db", default="data/inventory.db")
    parser.add_argument("--out", default="out/results.json")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    path = Path(args.invoice_path)
    files = sorted(p for p in path.iterdir() if p.is_file()) if path.is_dir() else [path]
    llm = None if args.offline else from_env()
    db = store.connect(args.db)
    db.execute("DELETE FROM invoices")  # each run starts with an empty payment history
    print(f"Processing {len(files)} invoice(s) with {'Grok' if llm else 'offline rules (no XAI_API_KEY)'}\n")

    results = []
    for f in files:
        r = process(f, db, llm)
        results.append(r)
        inv = r.get("invoice") or {}
        total = f"{inv['currency']} {inv['total']:,.2f}" if inv.get("total") is not None else ""
        print(f"{f.name}: {LABELS[r['decision']]}  {inv.get('invoice_number') or ''}  "
              f"{inv.get('vendor') or ''}  {total}".rstrip())
        for level, msg in r.get("issues", []):
            print(f"    [{level}] {msg}")
        for note in r.get("critique", []):
            print(f"    [critic] {note}")
        print()

    counts = {k: sum(r["decision"] == k for r in results) for k in LABELS}
    paid = sum(r["payment"].get("amount", 0) for r in results if r.get("payment"))
    print(f"{counts['approved']} paid (${paid:,.2f}), {counts['rejected']} rejected, "
          f"{counts['needs_review']} need review, {counts['error']} errors")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=2, default=str))
    print(f"Full results: {args.out}")


if __name__ == "__main__":
    main()
