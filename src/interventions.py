"""Human intervention: what a person decided, and what they changed.

Kept in the ledger database, not the run log. The run log is observability - useful and
disposable. A record of who authorised a deviation from the automated controls is evidence for
a payment, so pruning logs must not prune it.

**An intervention supplies a better input. It never supplies the verdict.** Resolving one
always produces a NEW run that goes through validation, routing, approval and the gate like
any other invoice. If a resolution resumed at the gate instead, a person would become the way
around every control in the system. A human correcting one field can easily introduce a second
problem, and the controls have to see the thing that actually gets paid.

Four resolutions, because "wrong" means four different things and they are not
interchangeable:

| Resolution | What the person is asserting | What changes |
|---|---|---|
| `misread` | the document is fine, we transcribed it wrong | the extracted field, then re-run |
| `vendor_reissue` | the invoice itself is wrong | nothing here; the vendor sends a new document |
| `records_wrong` | our catalogue or supplier list is out of date | our reference data, then re-run |
| `accept` | the findings are real and immaterial | nothing, but named findings are waived |

The one that matters is the absence of a fifth option: there is no "edit the amount and pay
it". Paying 250.00 against an invoice that says 2,500.00 means paying a figure no document
authorises - the vendor's receivable still says 2,500.00, nothing reconciles, and they will
chase the balance. A wrong bill is corrected by the vendor, which is why every remediation
line for those findings asks for a reissue rather than an adjustment.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ledger import DB_PATH

RESOLUTIONS = ("misread", "vendor_reissue", "records_wrong", "accept")

# Waivable means "a person may decide this does not matter". These are not.
#
# Paying an invoice that the ledger says is already paid is not a judgment call about
# materiality; it is money leaving twice for one debt. If a second payment genuinely is owed,
# it is owed against a new document, and that document gets its own run and its own ledger
# entry. A negative total is not an invoice at all.
NEVER_WAIVABLE = frozenset({
    "duplicate_invoice_number",
    "revises_paid_invoice",
    "negative_total",
})

SCHEMA = """
CREATE TABLE IF NOT EXISTS interventions (
    intervention_id TEXT PRIMARY KEY,
    at              TEXT NOT NULL,
    actor           TEXT NOT NULL,    -- who. Never optional: an unattributed override is
                                      -- not an audit record.
    run_id          TEXT NOT NULL,    -- the run being resolved
    invoice_number  TEXT,
    resolution      TEXT NOT NULL,
    justification   TEXT NOT NULL,    -- why, in their words. Also never optional.
    saw_findings    TEXT,             -- JSON: the findings in front of them at the time
    waived          TEXT,             -- JSON: the findings they chose to waive
    corrections     TEXT,             -- JSON: field -> {from, to}
    resulting_run   TEXT              -- the re-run this produced, once it exists
);
CREATE INDEX IF NOT EXISTS idx_int_invoice ON interventions(invoice_number);
CREATE INDEX IF NOT EXISTS idx_int_run ON interventions(run_id);
"""


def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


class InterventionError(Exception):
    """The intervention is not one we can accept. The message is written for a person."""


def record(
    run_id: str,
    invoice_number: str | None,
    resolution: str,
    actor: str,
    justification: str,
    saw_findings: list[str] | None = None,
    waived: list[str] | None = None,
    corrections: dict | None = None,
    db_path: Path = DB_PATH,
) -> str:
    """Write what a person decided. Returns the intervention id.

    Refuses rather than recording something an auditor could not act on: no actor, no
    justification, an unknown resolution, or a waiver of something that is not waivable.
    """
    if resolution not in RESOLUTIONS:
        raise InterventionError(
            f"{resolution!r} is not a resolution; use one of {', '.join(RESOLUTIONS)}")
    if not (actor or "").strip():
        raise InterventionError("an intervention needs an actor; an unattributed override is "
                                "not an audit record")
    if len((justification or "").strip()) < 10:
        raise InterventionError("an intervention needs a justification in your own words - "
                                "the next person reading this will have only that")

    waived = list(waived or [])
    forbidden = sorted(set(waived) & NEVER_WAIVABLE)
    if forbidden:
        raise InterventionError(
            f"these cannot be waived: {', '.join(forbidden)}. Paying against a record of a "
            f"prior payment is not a materiality judgement. If a further payment is owed, it "
            f"is owed against a new document, which gets its own run.")
    if resolution != "accept" and waived:
        raise InterventionError("findings are only waived by an 'accept' resolution")
    if resolution == "misread" and not corrections:
        raise InterventionError("a 'misread' resolution has to say what we read wrongly")

    iid = str(uuid.uuid4())[:8]
    conn = _connect(db_path)
    conn.execute(
        """INSERT INTO interventions (intervention_id, at, actor, run_id, invoice_number,
               resolution, justification, saw_findings, waived, corrections, resulting_run)
           VALUES (?,?,?,?,?,?,?,?,?,?,NULL)""",
        (iid, datetime.now(timezone.utc).isoformat(timespec="seconds"), actor.strip(),
         run_id, invoice_number, resolution, justification.strip(),
         json.dumps(sorted(set(saw_findings or []))), json.dumps(sorted(set(waived))),
         json.dumps(corrections or {})),
    )
    conn.commit()
    conn.close()
    return iid


def link_run(intervention_id: str, resulting_run: str, db_path: Path = DB_PATH) -> None:
    """Record which re-run an intervention produced, so the chain is traceable.

    "Why was this paid?" has to lead back through the intervention to the escalation that
    prompted it, or the override is invisible from the payment.
    """
    conn = _connect(db_path)
    conn.execute("UPDATE interventions SET resulting_run=? WHERE intervention_id=?",
                 (resulting_run, intervention_id))
    conn.commit()
    conn.close()


def get(intervention_id: str, db_path: Path = DB_PATH) -> sqlite3.Row | None:
    conn = _connect(db_path)
    row = conn.execute("SELECT * FROM interventions WHERE intervention_id=?",
                       (intervention_id,)).fetchone()
    conn.close()
    return row


def history(invoice_number: str | None, db_path: Path = DB_PATH) -> list[sqlite3.Row]:
    if not invoice_number:
        return []
    conn = _connect(db_path)
    rows = conn.execute("SELECT * FROM interventions WHERE invoice_number=? ORDER BY at",
                        (invoice_number,)).fetchall()
    conn.close()
    return rows


def waived_by(intervention_id: str | None, db_path: Path = DB_PATH) -> frozenset[str]:
    """The findings a specific intervention waived, and only those.

    Scoped deliberately to one intervention rather than to the invoice. A waiver covers the
    findings a person actually had in front of them; a problem that surfaces for the first
    time on the re-run was never seen by anyone and must not inherit someone else's approval.
    """
    if not intervention_id:
        return frozenset()
    row = get(intervention_id, db_path)
    if not row:
        return frozenset()
    return frozenset(json.loads(row["waived"] or "[]")) - NEVER_WAIVABLE


# Fields a person may correct. Deliberately the scalars and the three figures: those are what
# a misreading actually gets wrong. Line items are not correctable here - a line that was read
# wrongly enough to matter is a re-extraction, and a line that is wrong on the invoice is the
# vendor's to reissue.
CORRECTABLE_TEXT = ("invoice_number", "vendor", "issue_date", "due_date", "currency",
                    "revision", "notes")
CORRECTABLE_FIGURES = ("subtotal", "tax_amount", "total")


def apply_corrections(inv, corrections: dict, actor: str) -> tuple[object, list[str]]:
    """Put a person's corrections onto a freshly extracted invoice.

    Applied after extraction rather than before, because re-extracting produces the same
    misreading. The corrected invoice then goes through every check from validation onward.

    A corrected figure's citation becomes an attestation - "[corrected by someone]" - rather
    than text copied from the document. That is the honest record: the value is now vouched
    for by a person instead of transcribed, and anyone auditing it can see which. The figure
    still counts as stated, because it IS on the invoice; we simply read it wrongly.
    """
    from models import Cited  # local import: models does not need to know about this

    applied = []
    for field, raw in (corrections or {}).items():
        if field in CORRECTABLE_TEXT:
            before = getattr(inv, field)
            setattr(inv, field, str(raw) if raw not in (None, "", "null") else None)
            applied.append(f"{field}: {before!r} -> {getattr(inv, field)!r}")
        elif field in CORRECTABLE_FIGURES:
            before = getattr(inv, field)
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise InterventionError(f"{field} has to be a number, not {raw!r}")
            setattr(inv, field, Cited(value=value,
                                      source_text=f"[corrected by {actor.strip()}]"))
            applied.append(f"{field}: {before.value!r} -> {value!r}")
        else:
            raise InterventionError(
                f"{field!r} is not correctable here. Correctable fields are "
                f"{', '.join(CORRECTABLE_TEXT + CORRECTABLE_FIGURES)}. A wrong line item is "
                f"either a re-extraction or something for the vendor to reissue.")
    return inv, applied
