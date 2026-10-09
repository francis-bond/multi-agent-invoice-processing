"""Mock inventory database.

Schema and seed data are the minimum specified in the brief. The brief invites extension
(unit price, category, vendor tables) to support richer validation.
"""

import sqlite3
from pathlib import Path

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


def lookup(item: str, db_path: Path = DB_PATH) -> int | None:
    """Stock level for an item, or None if the item is not in inventory at all.

    None and 0 are different answers: "we do not stock this" versus "we stock it and have
    none". The brief treats them as different scenarios, so the return type has to carry
    the distinction.
    """
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT stock FROM inventory WHERE item = ?", (item,)).fetchone()
    conn.close()
    return row[0] if row else None


if __name__ == "__main__":
    setup()
    print(f"seeded {DB_PATH}")
    for item, _ in SEED:
        print(f"  {item:12} stock={lookup(item)}")
    print(f"  {'WidgetC':12} stock={lookup('WidgetC')}   <- not in inventory")
