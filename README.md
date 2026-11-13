# Acme invoice processing

A small multi-agent pipeline for the Galatiq case. It reads Acme's invoices, checks them against a SQLite inventory, runs a simulated VP approval with a critic, and then pays the invoice or logs why it didn't.

## Running it

```
pip install -r requirements.txt
python main.py --invoice_path=data/invoices/invoice_1001.txt
python main.py                  # all 20 sample invoices
python -m pytest -q             # tests
```

With `XAI_API_KEY` set it uses Grok (`grok-3` by default, or set `XAI_MODEL`). Without a key it runs fully offline on rules, which is how I developed and tested it, since the case says to assume no internet. Results print to the console and are saved to `out/results.json`.

## How it works

There are four agents, one file each in `invoices/`, run in order by `pipeline.py`:

1. Ingestion reads PDF, TXT, JSON, CSV and XML into one standard format. JSON, CSV and XML map straight to fields. For PDF and TXT, Grok extracts the fields using a JSON schema. If the numbers it returns don't add up, it gets one retry with the problem spelled out, so it can fix a misread value. Offline, a regex parser handles the samples, including the typos ("Vndr", "Amt") and OCR errors ("2O26").
2. Validation checks the invoice against the database with plain rules: items exist, quantities are within stock, prices are close to ours, totals add up, the vendor is approved, and the invoice hasn't already been paid. Each problem is either "reject" or "review".
3. Approval has a VP agent make the call. It can look up inventory through a tool. A critic then checks the VP's reasoning against the policy, and if it disagrees the VP gets one more round. Invoices over $10,000 need the critic to agree, or they go to a person.
4. Payment calls the case's `mock_payment` for approved invoices and records every outcome in the database.

## Decisions I made

- Validation never uses the LLM. Stock and arithmetic checks should be exact and testable. The model is useful for reading messy documents and writing the approval reasoning, not for deciding whether 20 is more than 15.
- The rules have the final say. The VP agent can make a decision more cautious but never less, so a confused or manipulated model can't approve an invoice the rules rejected.
- Item names are matched after removing spaces and case ("Widget A" is WidgetA), but never fuzzily. "WidgetC" is one letter from "WidgetA", and I'd rather reject it than pay for the wrong thing.
- Vendor names are fuzzy matched for the opposite reason: a near miss gets flagged. INV-1012 comes from "QuickShip Distributers", one letter off an approved vendor, and that's worth a phone call before paying.
- I wrote my own small loop instead of using LangGraph. The flow is four steps in a straight line, so a framework would have added more to explain than it saved.

## What it finds in the samples

5 paid ($19,140), 11 rejected and 4 sent to a person. Besides the cases listed in the brief, it caught:

- INV-1007's printed total is $110 less than its subtotal plus tax.
- INV-1013's total is $50 more than its parts, in both the JSON and the PDF.
- INV-1011 arrives twice (PDF and TXT) and is only paid once.
- INV-1004 has a revised version for a higher amount after the first one was paid, so it goes to a person.

## Assumptions and limits

- I added unit prices to the inventory table and a list of approved vendors. Fraudster LLC and NoProd Industries aren't on it.
- Stock is checked against stock on hand, as the brief describes. In a real AP process I'd match against the purchase order and what was actually received.
- Payments are USD only, so the EUR invoice goes to a person.
- "Needs review" is logged with the reason, but there's no screen for a person to approve it yet. That would be the next thing I'd build, probably as a Slack message with approve and reject buttons.
- The payment history is cleared at the start of each run so the demo is repeatable.
- I haven't run it against the live Grok API. The Grok paths are tested with a scripted stand-in.
