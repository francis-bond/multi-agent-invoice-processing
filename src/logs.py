"""Read the run log.

A SQLite file is not readable without a tool, so this is the tool. Two views: everything
that has run, and one run in full.

    uv run python src/logs.py                 list all runs
    uv run python src/logs.py <run_id>        one run, including the prompts sent
"""

import json
import sqlite3
import sys

from runlog import DB_PATH

OUTCOME_MARK = {"paid": "PAID", "rejected": "DENIED", "failed": "ERROR", None: "INCOMPLETE"}


def list_runs() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM runs ORDER BY started_at DESC").fetchall()
    if not rows:
        print("no runs recorded yet")
        return

    print(f"{'run':9} {'invoice':10} {'vendor':26} {'total':>10}  {'outcome':10} {'flags':>5}  {'ms':>6}")
    print("-" * 86)
    for r in rows:
        print(f"{r['run_id']:9} {str(r['invoice_number'] or '-')[:10]:10} "
              f"{str(r['vendor'] or '-')[:26]:26} {r['total'] or 0:>10,.2f}  "
              f"{OUTCOME_MARK[r['outcome']]:10} {r['flag_count'] or 0:>5}  {r['duration_ms'] or 0:>6}")

    print()
    for outcome, n, amt in conn.execute(
        "SELECT outcome, COUNT(*), SUM(total) FROM runs GROUP BY outcome"
    ):
        print(f"  {OUTCOME_MARK[outcome]:10} {n:3}   {amt or 0:>14,.2f}")


def show_run(run_id: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM runs WHERE run_id LIKE ?", (run_id + "%",)).fetchone()
    if not r:
        print(f"no run matching {run_id!r}")
        return

    print(f"run {r['run_id']}   {r['started_at']}   {r['duration_ms']}ms")
    print(f"  source    {r['source_path']}")
    print(f"  invoice   {r['invoice_number']}  from {r['vendor']}  for {r['total']:,.2f}"
          if r['total'] is not None else f"  invoice   {r['invoice_number']}  from {r['vendor']}")
    print(f"  outcome   {OUTCOME_MARK[r['outcome']]}"
          + ("   (run did not complete)" if r['outcome'] is None else ""))
    if r["processing_error"]:
        print(f"  error     {r['processing_error']}")

    flags = conn.execute("SELECT * FROM flags WHERE run_id=?", (r["run_id"],)).fetchall()
    print(f"\n  findings ({len(flags)})")
    for f in flags or []:
        print(f"    [{f['severity']:7}] {f['code']:24} {f['detail']}")
    if not flags:
        print("    none")

    print(f"\n  scrutiny  {'yes' if r['needs_scrutiny'] else 'no'}")
    if r["decision"]:
        print(f"  decision  {r['decision']}")
        print(f"  reasoning {r['reasoning']}")
    if r["blocked_reason"]:
        print(f"  blocked   {r['blocked_reason']}")

    print("\n  nodes")
    prompts = {}
    for s in conn.execute("SELECT * FROM steps WHERE run_id=? ORDER BY seq", (r["run_id"],)):
        detail = json.loads(s["detail"])
        prompts.update(detail.get("prompts", {}))
        keys = [k for k in detail if k != "prompts"]
        print(f"    {s['seq']}. {s['node']:10} produced: {', '.join(keys) or '(nothing)'}")

    for node, prompt in prompts.items():
        print(f"\n  prompt sent to the {node} agent")
        for line in prompt.splitlines():
            print(f"    | {line}")


if __name__ == "__main__":
    show_run(sys.argv[1]) if len(sys.argv) > 1 else list_runs()
