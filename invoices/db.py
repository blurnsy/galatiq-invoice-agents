"""SQLite: the inventory the case provides, plus two tables I added (approved
vendors, and a history of processed invoices to catch duplicates)."""

import re
import sqlite3
from difflib import SequenceMatcher

INVENTORY = [  # item, stock, unit price (prices are my addition)
    ("WidgetA", 15, 250.00),
    ("WidgetB", 10, 500.00),
    ("GadgetX", 5, 750.00),
    ("FakeItem", 0, 1000.00),
]

APPROVED_VENDORS = [
    "Widgets Inc.", "Gadgets Co.", "Precision Parts Ltd.", "Global Supply Chain Partners",
    "Acme Industrial Supplies", "MegaWidgets Corp", "Consolidated Materials Group",
    "Summit Manufacturing Co.", "QuickShip Distributors", "Atlas Industrial Supply",
    "TechParts International", "Reliable Components Inc.",
]


def connect(path="data/inventory.db"):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS inventory (item TEXT PRIMARY KEY, stock INTEGER, unit_price REAL);
        CREATE TABLE IF NOT EXISTS vendors (name TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY, invoice_number TEXT, vendor TEXT, total REAL,
            status TEXT, reason TEXT, source TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    """)
    db.executemany("INSERT OR IGNORE INTO inventory VALUES (?, ?, ?)", INVENTORY)
    db.executemany("INSERT OR IGNORE INTO vendors VALUES (?)", [(v,) for v in APPROVED_VENDORS])
    db.commit()
    return db


def squash(name):
    """'Widget A' -> 'widgeta', so spacing and case don't matter."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def find_item(db, name):
    for row in db.execute("SELECT * FROM inventory"):
        if squash(row["item"]) == squash(name):
            return dict(row)
    return None


def match_vendor(db, name):
    """Returns ("exact" | "close" | "unknown", best matching approved vendor)."""
    def clean(v):  # drop "Inc.", "Co." etc. before comparing
        return re.sub(r"\b(inc|co|corp|llc|ltd)\b|[^a-z0-9 ]", "", v.lower()).strip()

    best, score = None, 0.0
    for (vendor,) in db.execute("SELECT name FROM vendors"):
        s = SequenceMatcher(None, clean(name), clean(vendor)).ratio()
        if s > score:
            best, score = vendor, s
    if score == 1.0:
        return "exact", best
    return ("close" if score >= 0.85 else "unknown"), best


def history(db, invoice_number):
    return [dict(r) for r in db.execute(
        "SELECT * FROM invoices WHERE invoice_number = ? ORDER BY id", (invoice_number,))]


def record(db, invoice, status, reason):
    db.execute("INSERT INTO invoices (invoice_number, vendor, total, status, reason, source) "
               "VALUES (?, ?, ?, ?, ?, ?)",
               (invoice["invoice_number"], invoice["vendor"], invoice["total"], status, reason, invoice["source"]))
    db.commit()
