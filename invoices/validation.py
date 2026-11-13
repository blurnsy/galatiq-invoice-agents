"""Validation agent: plain rules against the database, no LLM.

Each issue is ("reject", message) for something we must not pay, or
("review", message) for something a person should look at first.
"""

from collections import defaultdict

from . import db as store
from .ingestion import arithmetic_problems

PRICE_TOLERANCE = 0.15  # more than 15% over our price needs a person to check the PO


def validate(invoice, db):
    issues = []
    reject = lambda msg: issues.append(("reject", msg))  # noqa: E731
    review = lambda msg: issues.append(("review", msg))  # noqa: E731

    # Required fields
    if not invoice["vendor"]:
        reject("No vendor name, so we can't tell who we'd be paying.")
    if not invoice["invoice_number"]:
        reject("No invoice number.")
    if invoice["total"] is None:
        reject("No total amount.")
    elif invoice["total"] <= 0:
        reject(f"Total is {invoice['total']:,.2f}; it must be positive.")
    if not invoice["items"]:
        reject("No line items found.")

    # Items against inventory. Quantities are added up per item, because an
    # invoice can split the same item across several lines.
    billed = defaultdict(float)
    for line in invoice["items"]:
        qty, price = line["quantity"], line["unit_price"]
        if qty is None or qty <= 0:
            reject(f"{line['item']}: quantity {qty} is not valid.")
            continue
        stock = store.find_item(db, line["item"])
        if stock is None:
            reject(f"{line['item']} is not in our inventory.")
            continue
        billed[stock["item"]] += qty
        if price and price > stock["unit_price"] * (1 + PRICE_TOLERANCE):
            note = f" (invoice says '{line['note']}')" if line["note"] else ""
            review(f"{stock['item']} billed at {price:,.2f}, our price is {stock['unit_price']:,.2f}{note}.")
    for item, qty in billed.items():
        stock = store.find_item(db, item)["stock"]
        if stock == 0:
            reject(f"{item} has zero stock, which suggests a fake or wrong item.")
        elif qty > stock:
            reject(f"{item}: {qty:g} billed but only {stock} in stock.")

    # Arithmetic
    for problem in arithmetic_problems(invoice):
        reject(problem[0].upper() + problem[1:] + ".")

    # Vendor
    if invoice["vendor"]:
        status, closest = store.match_vendor(db, invoice["vendor"])
        if status == "close":
            review(f"Vendor '{invoice['vendor']}' is almost but not exactly '{closest}'. "
                   "Could be a typo or impersonation; confirm with the vendor before paying.")
        elif status == "unknown":
            review(f"Vendor '{invoice['vendor']}' is not on the approved vendor list.")

    # Dates and currency
    if not invoice["due_date"]:
        raw = invoice["due_date_raw"]
        review(f"No usable due date (printed as '{raw}')." if raw else "No due date.")
    if invoice["currency"] != "USD":
        review(f"Invoice is in {invoice['currency']}; payments go out in USD, so someone needs to confirm the rate.")

    # Duplicates and revisions
    for prior in store.history(db, invoice["invoice_number"]):
        if prior["status"] == "paid" and prior["total"] == invoice["total"]:
            reject(f"Already paid from {prior['source']}; this is a duplicate.")
        elif prior["status"] == "paid":
            review(f"{invoice['invoice_number']} was already paid at {prior['total']:,.2f}; "
                   f"this version says {invoice['total']:,.2f}. Confirm the change before paying the difference.")
        elif prior["status"] == "needs_review":
            reject(f"Already waiting for review from {prior['source']}; not queuing it twice.")

    return issues
