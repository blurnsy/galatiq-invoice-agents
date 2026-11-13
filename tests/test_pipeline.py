import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from invoices import db as store  # noqa: E402
from invoices.grok import Grok  # noqa: E402
from invoices.ingestion import read_invoice  # noqa: E402
from invoices.pipeline import process  # noqa: E402

INVOICES = ROOT / "data" / "invoices"


@pytest.fixture
def db(tmp_path):
    return store.connect(str(tmp_path / "test.db"))


# Each sample run on its own, against an empty history.
EXPECTED = {
    "invoice_1001.txt": "approved",
    "invoice_1002.txt": "rejected",       # 20 GadgetX, 5 in stock
    "invoice_1003.txt": "rejected",       # FakeItem has zero stock
    "invoice_1004.json": "approved",
    "invoice_1004_revised.json": "approved",
    "invoice_1005.json": "rejected",      # 8 GadgetX, 5 in stock
    "invoice_1006.csv": "approved",
    "invoice_1007.csv": "rejected",       # over stock, total is 110 short
    "invoice_1008.txt": "rejected",       # unknown items
    "invoice_1009.json": "rejected",      # negative quantity, no vendor
    "invoice_1010.txt": "needs_review",   # rush price 20% over ours
    "invoice_1011.pdf": "approved",
    "invoice_1011.txt": "approved",
    "invoice_1012.pdf": "needs_review",   # vendor name one letter off
    "invoice_1012.txt": "needs_review",
    "invoice_1013.json": "rejected",      # over stock, total is 50 over
    "invoice_1013.pdf": "rejected",
    "invoice_1014.xml": "needs_review",   # EUR
    "invoice_1015.csv": "approved",
    "invoice_1016.json": "rejected",      # WidgetC doesn't exist
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_sample_invoices(name, db):
    result = process(INVOICES / name, db)
    assert result["decision"] == EXPECTED[name], result["issues"]
    assert (result["payment"]["status"] == "paid") == (EXPECTED[name] == "approved")


def test_messy_text_is_parsed():
    inv = read_invoice(INVOICES / "invoice_1012.txt")  # OCR errors, "INV 1012", "Widget A"
    assert inv["invoice_number"] == "INV-1012"
    assert inv["invoice_date"] == "2026-01-26"  # printed as 26-Jan-2O26
    assert [i["item"] for i in inv["items"]] == ["Widget A", "WidgetB", "Gadget X"]
    assert inv["total"] == 9975.00


def test_hidden_total_errors_are_caught(db):
    issues = process(INVOICES / "invoice_1007.csv", db)["issues"]
    assert any("off by -110.00" in msg for _, msg in issues)


def test_same_invoice_in_two_formats_is_paid_once(db):
    assert process(INVOICES / "invoice_1011.pdf", db)["decision"] == "approved"
    assert process(INVOICES / "invoice_1011.txt", db)["decision"] == "rejected"


def test_revised_invoice_goes_to_a_person(db):
    process(INVOICES / "invoice_1004.json", db)
    assert process(INVOICES / "invoice_1004_revised.json", db)["decision"] == "needs_review"


def test_unreadable_file_does_not_stop_the_batch(db, tmp_path):
    bad = tmp_path / "broken.json"
    bad.write_text("{not json")
    assert process(bad, db)["decision"] == "error"
    assert process(INVOICES / "invoice_1001.txt", db)["decision"] == "approved"


# ---------------------------------------------------------------- with a scripted model

class FakeGrok:
    """Returns queued answers per schema name, and calls the tools it's given."""

    def __init__(self, **answers):
        self.answers = answers
        self.calls = []

    def ask(self, messages, schema, name, tools=None):
        self.calls.append((name, messages))
        if tools:
            fn, _ = tools[0]
            fn(item="WidgetA")
        answer = self.answers[name].pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


APPROVE = {"decision": "approve", "reasoning": "Looks fine."}
AGREE = {"agree": True, "feedback": "OK."}


def test_rules_override_a_model_that_approves_a_bad_invoice(db):
    llm = FakeGrok(vp_decision=[APPROVE], critique=[AGREE])
    assert process(INVOICES / "invoice_1005.json", db, llm)["decision"] == "rejected"


def test_critic_pushback_gets_a_second_vp_round(db):
    llm = FakeGrok(vp_decision=[APPROVE, APPROVE],
                   critique=[{"agree": False, "feedback": "Check stock first."}, AGREE])
    result = process(INVOICES / "invoice_1006.csv", db, llm)
    assert result["decision"] == "approved"
    assert result["critique"] == ["Check stock first.", "OK."]
    second_vp_prompt = [m for n, m in llm.calls if n == "vp_decision"][1]
    assert "Check stock first." in second_vp_prompt[-1]["content"]


def test_large_invoice_without_critic_agreement_goes_to_a_person(db, tmp_path):
    big = tmp_path / "big.json"
    big.write_text(json.dumps({
        "invoice_number": "INV-2001", "vendor": "Widgets Inc.", "date": "2026-02-01", "due_date": "2026-03-03",
        "line_items": [{"item": "WidgetA", "quantity": 15, "unit_price": 250},
                       {"item": "WidgetB", "quantity": 10, "unit_price": 500}],
        "subtotal": 8750, "tax_amount": 1750, "total": 10500}))
    no = {"agree": False, "feedback": "Not convinced."}
    llm = FakeGrok(vp_decision=[APPROVE, APPROVE], critique=[no, no])
    assert process(big, db, llm)["decision"] == "needs_review"


def test_extraction_retries_when_numbers_dont_add_up(db):
    good = {"invoice_number": "INV-1001", "vendor": "Widgets Inc.", "invoice_date": "2026-01-15",
            "due_date": "2026-02-01", "currency": "USD", "subtotal": 5000, "tax": 0, "shipping": 0,
            "total": 5000, "items": [{"item": "WidgetA", "quantity": 10, "unit_price": 250, "note": None},
                                     {"item": "WidgetB", "quantity": 5, "unit_price": 500, "note": None}]}
    misread = {**good, "total": 50000}
    llm = FakeGrok(invoice=[misread, good], vp_decision=[APPROVE], critique=[AGREE])
    result = process(INVOICES / "invoice_1001.txt", db, llm)
    assert result["invoice"]["total"] == 5000
    assert "don't add up" in llm.calls[1][1][-1]["content"]


def test_api_failure_falls_back_to_rules(db):
    llm = FakeGrok(invoice=[RuntimeError("timeout")], vp_decision=[RuntimeError("timeout")])
    assert process(INVOICES / "invoice_1001.txt", db, llm)["decision"] == "approved"


def test_grok_client_runs_tool_calls():
    # Stand-in for the openai SDK: first a tool call, then the final JSON answer.
    tool_call = SimpleNamespace(id="c1", function=SimpleNamespace(name="check_inventory",
                                                                  arguments='{"item": "GadgetX"}'))
    replies = [SimpleNamespace(content=None, tool_calls=[tool_call]),
               SimpleNamespace(content='{"decision": "approve"}', tool_calls=None)]
    sent = []

    def create(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=replies.pop(0))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    def check_inventory(item):
        """Stock for an item."""
        return {"item": item, "stock": 5}

    out = Grok("key", client=client).ask([{"role": "user", "content": "hi"}], {"type": "object"}, "vp",
                                         tools=[(check_inventory, {"type": "object"})])
    assert out == {"decision": "approve"}
    assert sent[0]["response_format"]["json_schema"]["strict"] is True
    assert json.loads(sent[1]["messages"][-1]["content"]) == {"item": "GadgetX", "stock": 5}
