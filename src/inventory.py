"""Mock inventory database.

Schema and seed data are the minimum specified in the brief. The brief invites extension
(unit price, category, vendor tables) to support richer validation.
"""

import re
import sqlite3
from pathlib import Path
from typing import NamedTuple

DB_PATH = Path(__file__).parent.parent / "inventory.db"

SEED = [
    ("WidgetA", 15),
    ("WidgetB", 10),
    ("GadgetX", 5),
    ("FakeItem", 0),
]


def setup(db_path: Path = DB_PATH) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS inventory (item TEXT PRIMARY KEY, stock INTEGER)")
    conn.executemany("INSERT OR REPLACE INTO inventory VALUES (?, ?)", SEED)
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

    @property
    def found(self) -> bool:
        return self.item is not None

    @property
    def is_loose(self) -> bool:
        """True when matching required discarding part of the name."""
        return self.how == "annotation"


def resolve(item: str, db_path: Path = DB_PATH) -> Match:
    """Find an invoice line item in the catalogue, widening the match in careful stages.

    Exact first, then ignoring case and separators, then ignoring a trailing annotation.
    Each stage is reported, so a caller can treat a clean match differently from one that
    needed information thrown away.

    The catalogue is read whole and keyed in memory. That is fine at this size; a real
    catalogue would carry an indexed canonical column so the database does the matching.
    """
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT item, stock FROM inventory").fetchall()
    conn.close()

    by_exact = {name: stock for name, stock in rows}
    if item in by_exact:
        return Match(by_exact[item], item, "exact")

    by_canonical = {canonical(name): (name, stock) for name, stock in rows}
    hit = by_canonical.get(canonical(item))
    if hit:
        return Match(hit[1], hit[0], "normalized")

    bare = strip_annotation(item)
    if bare != item:
        hit = by_canonical.get(canonical(bare))
        if hit:
            return Match(hit[1], hit[0], "annotation")

    return Match(None, None, "unmatched")


def lookup(item: str, db_path: Path = DB_PATH) -> int | None:
    """Stock level for an item, or None if it is not in the catalogue at all.

    None and 0 are different answers: "we do not stock this" versus "we stock it and have
    none". The brief treats them as different scenarios, so the return type has to carry
    the distinction.
    """
    return resolve(item, db_path).stock


if __name__ == "__main__":
    setup()
    print(f"seeded {DB_PATH}")
    for item, _ in SEED:
        print(f"  {item:12} stock={lookup(item)}")
    for probe in ("WidgetA", "Widget A", "widget-a", "WidgetA (rush order)", "WidgetC"):
        m = resolve(probe)
        print(f"  {probe:22} -> {m.item or '(no match)':10} stock={m.stock} via {m.how}")
