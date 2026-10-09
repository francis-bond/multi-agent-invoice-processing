"""The payments ledger.

Deliberately a separate database from runs.db. Run logs are observability - useful, and
disposable. This is the business record of money that has left the account, and it is the
thing that must survive. Mixing them would mean a decision to prune logs could delete the
record of what was paid.

Queried before paying, not only written after. A ledger you only append to tells you what
happened; a ledger you consult stops it happening twice.
"""

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from models import ExtractedInvoice
from policy import HOME_CURRENCY

DB_PATH = Path(__file__).parent.parent / "ledger.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    run_id         TEXT PRIMARY KEY,
    paid_at        TEXT NOT NULL,
    invoice_number TEXT,
    vendor         TEXT,
    amount         REAL,
    currency       TEXT,       -- what was actually paid. An amount without a unit is not a
                               -- payment record.
    revision       TEXT,
    fingerprint    TEXT,       -- vendor + amount + line items, for catching a redelivered
                               -- invoice under a new number
    issue_date     TEXT
);
CREATE INDEX IF NOT EXISTS idx_pay_number ON payments(invoice_number, vendor);
CREATE INDEX IF NOT EXISTS idx_pay_fingerprint ON payments(fingerprint);
"""


def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # CREATE TABLE IF NOT EXISTS will not add a column to a ledger that already exists, and
    # this is the one database that must not be recreated to pick up a change.
    if "currency" not in {r["name"] for r in conn.execute("PRAGMA table_info(payments)")}:
        conn.execute("ALTER TABLE payments ADD COLUMN currency TEXT")
        conn.commit()
    return conn


def fingerprint(inv: ExtractedInvoice) -> str:
    """Identity of an invoice independent of its number.

    Vendor, currency, amount and line items. Deliberately NOT the date: a vendor re-billing
    the same order a week later under a new number is exactly what this is meant to catch,
    and including the date would let that slip through. The cost is that a genuine recurring
    order looks like a duplicate - so this produces a flag for a human, never a rejection.

    Currency is part of it because an amount is not a quantity of money without one. Without
    it a 500 EUR invoice and a 500 USD invoice for the same goods fingerprint identically,
    and one would be reported as a duplicate of the other.
    """
    items = sorted((li.item, li.quantity, li.unit_price) for li in inv.line_items)
    unit = (inv.currency or HOME_CURRENCY).upper()
    basis = f"{(inv.vendor or '').strip().lower()}|{unit}|{inv.total.value}|{items}"
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def prior_by_number(inv: ExtractedInvoice, db_path: Path = DB_PATH) -> list[sqlite3.Row]:
    if not inv.invoice_number:
        return []
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT * FROM payments WHERE invoice_number=? AND vendor IS ?",
        (inv.invoice_number, inv.vendor),
    ).fetchall()
    conn.close()
    return rows


def prior_by_content(inv: ExtractedInvoice, db_path: Path = DB_PATH) -> list[sqlite3.Row]:
    """Same content, different number."""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT * FROM payments WHERE fingerprint=? AND invoice_number IS NOT ?",
        (fingerprint(inv), inv.invoice_number),
    ).fetchall()
    conn.close()
    return rows


def record(run_id: str, inv: ExtractedInvoice, db_path: Path = DB_PATH) -> None:
    conn = _connect(db_path)
    # Columns named rather than positional. A ledger migrated with ALTER TABLE has its new
    # column at the end while a freshly created one has it mid-table, so a positional insert
    # would write the right values into the wrong columns on one of them.
    conn.execute(
        """INSERT OR REPLACE INTO payments
               (run_id, paid_at, invoice_number, vendor, amount, currency, revision,
                fingerprint, issue_date)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            run_id,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            inv.invoice_number,
            inv.vendor,
            inv.total.value if inv.total.was_stated else None,
            (inv.currency or HOME_CURRENCY).upper(),
            inv.revision,
            fingerprint(inv),
            inv.issue_date,
        ),
    )
    conn.commit()
    conn.close()
