"""Runs one invoice through the four agents in order."""

import logging

from .approval import approve
from .ingestion import read_invoice
from .payment import settle
from .validation import validate

log = logging.getLogger(__name__)


def process(path, db, llm=None):
    result = {"file": str(path)}
    try:
        invoice = read_invoice(path, llm)
    except Exception as exc:  # unreadable file: report it and move on to the next one
        log.error("Could not read %s: %s", path, exc)
        return {**result, "decision": "error", "reasoning": f"Could not read the file: {exc}"}

    issues = validate(invoice, db)
    approval = approve(invoice, issues, db, llm)
    payment = settle(invoice, approval, db)
    return {**result, "invoice": invoice, "issues": issues, **approval, "payment": payment}
