"""Run logging to SQLite.

Why SQLite rather than a file per run: the questions worth asking span runs. "Show me
everything denied last week." "What is waiting on a human?" A directory of JSON files
cannot answer those without reading all of them.

Why a wrapper around the graph rather than logging inside each node: one place to forget
instead of six.

Why writes happen after every node rather than once at the end: a run that crashes at node
four is the run whose log you most want, and a logger that only writes on completion produces
nothing for exactly that case. The run row is inserted before the first node executes, each
step is committed as it lands, and the row is completed at the end. A run with no outcome is
one that died mid-flight - which the dashboard should surface as needing attention.

What it has to answer, three months later: "why did Acme pay $5,000 to Widgets Inc. on
the 8th?" That means reasoning is recorded for approvals, not only rejections - the
expensive failure is a payment that should not have happened, not a rejection that should
not have.
"""

import json
import sqlite3

from pydantic import BaseModel
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "runs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id           TEXT PRIMARY KEY,
    started_at       TEXT NOT NULL,
    duration_ms      INTEGER,
    source_path      TEXT,
    invoice_number   TEXT,
    vendor           TEXT,
    total            REAL,
    currency         TEXT,
    outcome          TEXT,     -- paid | rejected | failed
    decision         TEXT,     -- approve | reject
    reasoning        TEXT,     -- recorded both ways, not only on rejection
    blocked_reason   TEXT,     -- why the gate refused, if it did
    processing_error TEXT,     -- the system failed, as opposed to the invoice being bad
    needs_scrutiny   INTEGER,
    flag_count       INTEGER
);
CREATE TABLE IF NOT EXISTS steps (
    run_id  TEXT NOT NULL,
    seq     INTEGER NOT NULL,
    node    TEXT NOT NULL,
    detail  TEXT,              -- JSON: whatever that node produced, prompts included
    PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS flags (
    run_id   TEXT NOT NULL,
    code     TEXT NOT NULL,
    detail   TEXT,
    severity TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_outcome ON runs(outcome);
CREATE INDEX IF NOT EXISTS idx_flags_code ON flags(code);
"""


def _encode(obj):
    """Serialise Pydantic models properly.

    json.dumps(..., default=str) silently turns a model into its repr, which looks like
    data in the log and cannot be parsed back. Anything genuinely unserialisable still
    falls through to str().
    """
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    return str(obj)


def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def start_run(run_id: str, source_path: str, db_path: Path = DB_PATH) -> None:
    """Insert the row before anything runs, so a crash still leaves a record.

    outcome stays NULL until the run completes. A NULL outcome means the run died.
    """
    conn = _connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO runs (run_id, started_at, source_path) VALUES (?,?,?)",
        (run_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), source_path),
    )
    conn.commit()
    conn.close()


def write_step(run_id: str, seq: int, node: str, detail: dict,
               db_path: Path = DB_PATH) -> None:
    """Commit one node's output as it lands, not at the end of the run."""
    conn = _connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO steps VALUES (?,?,?,?)",
        (run_id, seq, node, json.dumps(detail, default=_encode)),
    )
    conn.commit()
    conn.close()


def finish_run(state: dict, duration_ms: int, db_path: Path = DB_PATH) -> None:
    """Complete the row once the run ends. Only now does outcome stop being NULL."""
    inv = state.get("invoice")
    flags = state.get("flags", [])
    run_id = state.get("run_id")

    if state.get("processing_error"):
        outcome = "failed"
    elif state.get("payment_result"):
        outcome = "paid"
    else:
        outcome = "rejected"

    conn = _connect(db_path)
    conn.execute(
        """UPDATE runs SET duration_ms=?, invoice_number=?, vendor=?, total=?, currency=?,
                           outcome=?, decision=?, reasoning=?, blocked_reason=?,
                           processing_error=?, needs_scrutiny=?, flag_count=?
           WHERE run_id=?""",
        (
            duration_ms,
            inv.invoice_number if inv else None,
            inv.vendor if inv else None,
            inv.total.value if inv and inv.total.was_stated else None,
            inv.currency if inv else None,
            outcome,
            state.get("approval_decision"),
            state.get("approval_reasoning"),
            state.get("rejection_reason"),
            state.get("processing_error"),
            int(bool(state.get("needs_scrutiny"))),
            len(flags),
            run_id,
        ),
    )
    conn.execute("DELETE FROM flags WHERE run_id=?", (run_id,))
    conn.executemany(
        "INSERT INTO flags VALUES (?,?,?,?)",
        [(run_id, f["code"], f["detail"], f["severity"]) for f in flags],
    )
    conn.commit()
    conn.close()
