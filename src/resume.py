"""Carrying out a person's decision about an escalated invoice.

The decision is recorded first and acted on second, so the record exists even if the re-run
fails. Everything a person does here supplies a better input - a corrected reading, corrected
reference data, or an accepted finding - and the re-run goes through validation, routing,
approval and the gate exactly like a fresh invoice.

Nothing here resumes mid-pipeline. If it did, a person would be the way around every control
in the system, which is the opposite of what an approval queue is for.
"""

import sqlite3
from pathlib import Path

import interventions
from graph import process
from runlog import DB_PATH


def _run_row(run_id: str, db_path: Path = DB_PATH) -> sqlite3.Row:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        # Accept a unique prefix, because nobody wants to retype a run id from a dashboard.
        matches = conn.execute("SELECT * FROM runs WHERE run_id LIKE ?",
                               (run_id + "%",)).fetchall()
        if len(matches) == 1:
            row = matches[0]
        elif len(matches) > 1:
            conn.close()
            raise interventions.InterventionError(
                f"{run_id!r} matches {len(matches)} runs; give more of the id")
    conn.close()
    if row is None:
        raise interventions.InterventionError(f"no run {run_id!r} in the log")
    return row


def findings_of(run_id: str, db_path: Path = DB_PATH) -> list[str]:
    """The findings that were on screen when the person decided.

    Recorded with the intervention so a waiver can be scoped to what they actually saw.
    """
    conn = sqlite3.connect(db_path)
    codes = [r[0] for r in conn.execute("SELECT code FROM flags WHERE run_id=?", (run_id,))]
    conn.close()
    return sorted(set(codes))


def resolve(
    run_id: str,
    resolution: str,
    actor: str,
    justification: str,
    corrections: dict | None = None,
    waive: list[str] | None = None,
    log: bool = True,
) -> dict:
    """Record a decision and, where it calls for one, produce the re-run that carries it out.

    Returns a dict with the intervention id and the resulting state, if any.
    """
    row = _run_row(run_id)
    saw = findings_of(row["run_id"])

    unseen = sorted(set(waive or []) - set(saw))
    if unseen:
        # A waiver covers what was in front of someone. Waiving a finding that was never
        # raised is either a typo or an attempt to pre-authorise something unexamined.
        raise interventions.InterventionError(
            f"cannot waive findings this run did not raise: {', '.join(unseen)}. "
            f"It raised: {', '.join(saw) or 'none'}")

    iid = interventions.record(
        run_id=row["run_id"],
        invoice_number=row["invoice_number"],
        resolution=resolution,
        actor=actor,
        justification=justification,
        saw_findings=saw,
        waived=waive,
        corrections=corrections,
    )

    if resolution == "vendor_reissue":
        # Nothing to re-run. The invoice is wrong, and a corrected bill is a different
        # document: it arrives as its own file, gets its own run and its own ledger entry.
        # Re-running this one would just reproduce the same findings.
        return {"intervention_id": iid, "state": None,
                "note": ("recorded. The vendor has to reissue; process the corrected "
                         "document as a new invoice when it arrives.")}

    state = process(
        row["source_path"],
        log=log,
        intervention_id=iid,
        supersedes_run=row["run_id"],
        corrections=corrections or {},
        corrected_by=actor,
        waived=interventions.waived_by(iid),
        waiver=({"actor": actor, "justification": justification,
                 "waived": sorted(interventions.waived_by(iid))}
                if interventions.waived_by(iid) else None),
    )
    if log:
        interventions.link_run(iid, state["run_id"])
    return {"intervention_id": iid, "state": state}
