"""Mock inventory database.

The schema is deliberately minimal: an item and a stock level. Extending it with unit
prices, categories or vendor tables supports richer validation.
"""

import re
import sqlite3
from pathlib import Path
from typing import NamedTuple


# Database paths resolve when a function is CALLED, not when it is defined. A default of
# `db_path: Path = DB_PATH` binds the module global once at import, so pointing DB_PATH at a
# temporary file afterwards has no effect and the function quietly keeps using the real
# database. That cost two debugging sessions: a lookup that returned "no payment recorded"
# against a ledger that plainly had one, and a test that passed for the wrong reason.
DB_PATH = Path(__file__).parent.parent / "inventory.db"

# Item, stock on hand, agreed unit price. The price is what makes an overcharge detectable:
# without it, a WidgetA billed at 2,500.00 instead of 250.00 passes every other check, because
# the arithmetic is internally consistent and the quantity is within stock.
SEED = [
    ("WidgetA", 15, 250.00),
    ("WidgetB", 10, 500.00),
    ("GadgetX", 5, 750.00),
    ("FakeItem", 0, 100.00),
]

# The approved supplier list. In production this comes from the ERP vendor master; here it is
# reference data like the catalogue.
#
# An invoice from a name that is not on it is held for a person rather than refused outright,
# for the same reason an unknown item is: the list can be out of date, and a new supplier is
# more likely than a fraudulent one. But paying a counterparty nobody has approved is exactly
# the failure an accounts payable control exists to prevent, so it is never silent.
VENDORS = [
    "Acme Industrial Supplies",
    "Atlas Industrial Supply",
    "Consolidated Materials Group",
    "Gadgets Co.",
    "Global Supply Chain Partners",
    "MegaWidgets Corp",
    "Precision Parts Ltd.",
    "QuickShip Distributers",
    "Reliable Components Inc.",
    "Summit Manufacturing Co.",
    "TechParts International",
    "Widgets Inc.",
]


def setup(db_path: Path | None = None) -> None:
    db_path = db_path or DB_PATH
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS inventory (
                        item TEXT PRIMARY KEY, stock INTEGER, unit_price REAL)""")
    conn.execute("CREATE TABLE IF NOT EXISTS vendors (name TEXT PRIMARY KEY, status TEXT)")
    # An inventory table created before unit_price existed will not gain it from CREATE TABLE
    # IF NOT EXISTS.
    if "unit_price" not in {r[1] for r in conn.execute("PRAGMA table_info(inventory)")}:
        conn.execute("ALTER TABLE inventory ADD COLUMN unit_price REAL")
    conn.executemany(
        "INSERT OR REPLACE INTO inventory (item, stock, unit_price) VALUES (?,?,?)", SEED)
    conn.executemany("INSERT OR REPLACE INTO vendors (name, status) VALUES (?, 'approved')",
                     [(v,) for v in VENDORS])
    conn.commit()
    conn.close()


# A trailing parenthesised or bracketed qualifier: "WidgetA (rush order)", "WidgetB [BO]".
_ANNOTATION = re.compile(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]\s*$")


def canonical(name: str) -> str:
    """A matching key that ignores how a vendor chose to space and punctuate a name.

    "Widget A", "widget-a" and "WIDGETA" are the same product. Case and separators carry no
    product identity, so folding them away is a transform, not a guess: the result either
    matches a catalogue entry exactly or it does not. Nothing is discarded, because the
    original string is always still in hand for reporting.
    """
    return re.sub(r"[\s\-_.]+", "", name).casefold()


def strip_annotation(name: str) -> str:
    """Drop a trailing qualifier: "WidgetA (rush order)" -> "WidgetA".

    Unlike canonical(), this DISCARDS information, and the discarded part may matter. A rush
    order can carry a different price, so a match that needed this is reported as a loose
    match for a person to confirm rather than treated as clean.
    """
    return _ANNOTATION.sub("", name).strip()


class Match(NamedTuple):
    """The outcome of resolving an invoice line item against the catalogue."""

    stock: int | None  # None means not stocked at all; 0 means stocked and empty
    item: str | None  # the catalogue's own spelling, once matched
    how: str  # exact | normalized | annotation | unmatched
    unit_price: float | None = None  # the agreed price, for detecting an overcharge

    @property
    def found(self) -> bool:
        return self.item is not None

    @property
    def is_loose(self) -> bool:
        """True when matching required discarding part of the name."""
        return self.how == "annotation"


def resolve(item: str, db_path: Path | None = None) -> Match:
    """Find an invoice line item in the catalogue, widening the match in careful stages.

    Exact first, then ignoring case and separators, then ignoring a trailing annotation.
    Each stage is reported, so a caller can treat a clean match differently from one that
    needed information thrown away.

    The catalogue is read whole and keyed in memory. That is fine at this size; a real
    catalogue would carry an indexed canonical column so the database does the matching.
    """
    db_path = db_path or DB_PATH
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT item, stock, unit_price FROM inventory").fetchall()
    conn.close()

    by_exact = {name: (stock, price) for name, stock, price in rows}
    if item in by_exact:
        stock, price = by_exact[item]
        return Match(stock, item, "exact", price)

    by_canonical = {canonical(name): (name, stock, price) for name, stock, price in rows}
    hit = by_canonical.get(canonical(item))
    if hit:
        return Match(hit[1], hit[0], "normalized", hit[2])

    bare = strip_annotation(item)
    if bare != item:
        hit = by_canonical.get(canonical(bare))
        if hit:
            return Match(hit[1], hit[0], "annotation", hit[2])

    return Match(None, None, "unmatched")


def vendor_is_approved(name: str | None, db_path: Path | None = None) -> bool:
    """Is this name on the approved supplier list?

    Matched the same way item names are: ignoring case and punctuation, so "Widgets Inc." and
    "Widgets Inc" are one supplier rather than one approved and one unknown.
    """
    db_path = db_path or DB_PATH
    if not name or not name.strip():
        return False
    conn = sqlite3.connect(db_path)
    rows = [r[0] for r in conn.execute("SELECT name FROM vendors WHERE status = 'approved'")]
    conn.close()
    return canonical(name) in {canonical(v) for v in rows}


def ensure(db_path: Path | None = None) -> None:
    """Seed the catalogue if it is not there yet.

    The database is gitignored, so a fresh clone has none and the first lookup would fail
    with "no such table". The baseline catalogue is fixed reference data, so creating it on
    demand loses nothing and means the documented command works on a clean checkout. Existing
    stock levels are left alone.
    """
    db_path = db_path or DB_PATH
    conn = sqlite3.connect(db_path)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='inventory'"
        ).fetchone()
        seeded = exists and conn.execute("SELECT 1 FROM inventory LIMIT 1").fetchone()
        if seeded:
            # A catalogue seeded before unit_price and vendors existed is present but not
            # usable, so treat a missing price as unseeded.
            cols = {r[1] for r in conn.execute("PRAGMA table_info(inventory)")}
            priced = "unit_price" in cols and conn.execute(
                "SELECT 1 FROM inventory WHERE unit_price IS NOT NULL LIMIT 1").fetchone()
            seeded = bool(priced)
    finally:
        conn.close()
    if not seeded:
        setup(db_path)


def lookup(item: str, db_path: Path | None = None) -> int | None:
    """Stock level for an item, or None if it is not in the catalogue at all.

    None and 0 are different answers: "we do not stock this" versus "we stock it and have
    none". Those call for different responses, so the return type has to carry the
    distinction.
    """
    db_path = db_path or DB_PATH
    return resolve(item, db_path).stock


if __name__ == "__main__":
    setup()
    print(f"seeded {DB_PATH}")
    for item, _, price in SEED:
        print(f"  {item:12} stock={lookup(item):<4} price={price:,.2f}")
    print(f"  {len(VENDORS)} approved vendors")
    for probe in ("WidgetA", "Widget A", "widget-a", "WidgetA (rush order)", "WidgetC"):
        m = resolve(probe)
        print(f"  {probe:22} -> {m.item or '(no match)':10} stock={m.stock} via {m.how}")
