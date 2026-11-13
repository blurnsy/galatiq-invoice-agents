"""Payment agent: pay approved invoices, log everything else with its reason."""

import logging

from . import db as store

log = logging.getLogger(__name__)


def mock_payment(vendor, amount):
    """The mock banking API from the case."""
    print(f"Paid {amount} to {vendor}")
    return {"status": "success"}


def settle(invoice, approval, db):
    if approval["decision"] != "approved":
        store.record(db, invoice, approval["decision"], approval["reasoning"])
        log.info("%s %s: %s", invoice["invoice_number"], approval["decision"], approval["reasoning"])
        return {"status": "not paid"}

    result = mock_payment(invoice["vendor"], invoice["total"])
    if result.get("status") != "success":
        store.record(db, invoice, "payment_failed", str(result))
        return {"status": "payment failed"}
    store.record(db, invoice, "paid", approval["reasoning"])
    return {"status": "paid", "amount": invoice["total"]}
