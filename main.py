"""Command line entry point.

    python main.py --invoice_path=data/invoices/invoice_1001.txt
    python main.py --all
    python main.py --resume <run_id> --resolution misread --actor "A Name" \
        --justification "why" --set total=7185.00
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, "src")

import interventions  # noqa: E402
import inventory  # noqa: E402
import llm  # noqa: E402
from llm import MissingKeyError  # noqa: E402
import resume as resume_mod  # noqa: E402
from graph import process  # noqa: E402
from reasons import category, headline, remediation  # noqa: E402

OUTCOME_OF = {"paid": "PAID", "escalated": "ESCALATED", "rejected": "DENIED"}


def outcome_of(result: dict) -> str:
    if result.get("payment_result"):
        return "paid"
    if result.get("escalation_reason"):
        return "escalated"
    return "rejected"


def report(result: dict, source: str) -> None:
    """Everything one invoice did, in the order it happened."""
    if result.get("processing_error"):
        print(f"FAILED  {source}\n  {result['processing_error']}")
        return

    inv = result["invoice"]
    flags = result.get("flags", [])
    flags_d = [dict(f) for f in flags]
    unit = inv.currency or ""

    print(f"run {result['run_id']}  {source}  (read as {result.get('source_format')})")
    if result.get("supersedes_run"):
        print(f"  carrying out intervention {result.get('intervention_id')} "
              f"on run {result['supersedes_run']}")
    for change in result.get("corrections_applied", []):
        print(f"    corrected  {change}")

    print(f"  {inv.invoice_number}  {inv.vendor}  total={inv.total.value} {unit}".rstrip())
    for li in inv.line_items:
        note = f"   [{li.note}]" if li.note else ""
        print(f"    {li.item} x{li.quantity} @ {li.unit_price}{note}")
    for c in inv.charges:
        print(f"    {c.label}: {c.amount:,.2f}")

    if not flags:
        print("  validation: clean")
    else:
        print(f"  validation: {len(flags)} flag(s)")
        for f in flags:
            print(f"    [{f['severity']:7}] {f['code']:24} {f['detail']}")
    print(f"  scrutiny: {'yes' if result.get('needs_scrutiny') else 'no'}")

    for c in result.get("critiques", []):
        print(f"  critic round {c['round']}: reviewing {c['reviewed_decision']}, "
              f"{len(c['verified'])} verified, {len(c['grounded'])} grounded, "
              f"{len(c['discarded'])} discarded")
        for o in c["grounded"]:
            print(f"    objection on {o['targets']}: {o['problem']}")
            print(f"      quoted: {o['quote'].strip()!r}")
        for o in c["discarded"]:
            print(f"    discarded ({o['targets']}): {o['quote'].strip()[:55]!r}")

    if result.get("approval_decision"):
        print(f"  decision: {result['approval_decision']}")
        print(f"    {result['approval_reasoning']}")

    if result.get("escalation_reason"):
        print(f"  ESCALATED, not paid: {result['escalation_reason']}")
    elif result.get("rejection_reason"):
        print(f"  NOT PAID: {result['rejection_reason']}")
    elif result.get("payment_result"):
        pr = result["payment_result"]
        print(f"  paid: {pr['amount']:,.2f} {pr.get('currency', '')}".rstrip())

    oc = outcome_of(result)
    actor = None
    if result.get("intervention_id"):
        row = interventions.get(result["intervention_id"])
        actor = row["actor"] if row else None
    print(f"  queue: {category(oc, result.get('processing_error'), flags_d)}")
    print(f"  reason: {headline(oc, result.get('rejection_reason'), None, flags_d, result.get('approval_decision'), result.get('escalation_reason'), result.get('critique_rounds'), actor)}")
    if result.get("approval_action"):
        print(f"  next step: {result['approval_action']}")
    for name, advice in remediation(flags_d):
        print(f"    - {name}: {advice}")

    if oc != "paid":
        print(f"\n  Once a person has decided, record it:\n"
              f"    uv run python main.py --resume {result['run_id']} "
              f"--resolution <{'|'.join(interventions.RESOLUTIONS)}> \\\n"
              f"        --actor \"your name\" --justification \"why\"")


def run_one(path: str) -> None:
    report(process(path), path)
    print("\n  Audit view of every run:  uv run python src/dashboard.py")


def run_all(directory: str) -> None:
    """Process every invoice in a directory, newest-looking last.

    Ordered by filename so a revision follows the invoice it revises. Either order is caught -
    the original first gives `revises_paid_invoice`, the revision first gives
    `duplicate_invoice_number` - but the first order is the one that needs no unwinding.
    """
    files = sorted(p for p in Path(directory).iterdir() if p.is_file())
    if not files:
        print(f"no files in {directory}")
        raise SystemExit(1)

    print(f"Processing {len(files)} files from {directory}\n")
    rows, failed = [], 0
    for i, path in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {path.name}")
        try:
            result = process(str(path))
        except Exception as exc:                      # one bad file must not end the batch
            print(f"  ERROR: {exc}")
            failed += 1
            continue
        if result.get("processing_error"):
            print(f"  skipped: {result['processing_error']}")
            rows.append((path.name, "-", "-", "FAILED", result["processing_error"][:44]))
            continue
        inv, oc = result["invoice"], outcome_of(result)
        flags_d = [dict(f) for f in result.get("flags", [])]
        rows.append((
            path.name,
            inv.invoice_number or "-",
            f"{inv.total.value:,.2f} {inv.currency or ''}".strip()
            if inv.total.was_stated else "-",
            OUTCOME_OF[oc],
            headline(oc, result.get("rejection_reason"), None, flags_d,
                     result.get("approval_decision"), result.get("escalation_reason"),
                     result.get("critique_rounds"))[:44],
        ))
        print(f"  {OUTCOME_OF[oc]}")

    print(f"\n{'file':30} {'invoice':11} {'total':>14}  {'result':10} why")
    print("-" * 114)
    for name, number, total, mark, why in rows:
        print(f"{name:30} {number:11} {total:>14}  {mark:10} {why}")

    paid = sum(1 for r in rows if r[3] == "PAID")
    print(f"\n{paid} paid, {sum(1 for r in rows if r[3] == 'DENIED')} denied, "
          f"{sum(1 for r in rows if r[3] == 'ESCALATED')} escalated, "
          f"{sum(1 for r in rows if r[3] == 'FAILED') + failed} failed")
    print("\n  Audit view:  uv run python src/dashboard.py")


def run_resume(args) -> None:
    corrections = {}
    for pair in args.set or []:
        if "=" not in pair:
            print(f"--set needs field=value, got {pair!r}")
            raise SystemExit(2)
        field, value = pair.split("=", 1)
        corrections[field.strip()] = value.strip()

    try:
        outcome = resume_mod.resolve(
            run_id=args.resume,
            resolution=args.resolution,
            actor=args.actor,
            justification=args.justification,
            corrections=corrections or None,
            waive=args.waive or None,
        )
    except interventions.InterventionError as exc:
        print(f"refused: {exc}")
        raise SystemExit(2)

    print(f"recorded intervention {outcome['intervention_id']} "
          f"({args.resolution}) by {args.actor}\n")
    if outcome.get("note"):
        print(f"  {outcome['note']}")
        return
    report(outcome["state"], outcome["state"]["source_path"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process invoices end to end, or carry out a decision on one.")
    parser.add_argument("--invoice_path", help="a single invoice to process")
    parser.add_argument("--all", nargs="?", const="data/invoices", metavar="DIR",
                        help="process every invoice in a directory (default data/invoices)")
    parser.add_argument("--resume", metavar="RUN_ID",
                        help="record a decision about a run and act on it")
    parser.add_argument("--resolution", choices=interventions.RESOLUTIONS)
    parser.add_argument("--actor", help="who is making this decision")
    parser.add_argument("--justification", help="why, in your own words")
    parser.add_argument("--set", action="append", metavar="FIELD=VALUE",
                        help="correct a misread field (repeatable)")
    parser.add_argument("--waive", action="append", metavar="CODE",
                        help="a finding you accept as immaterial (repeatable)")
    args = parser.parse_args()

    chosen = [bool(args.invoice_path), bool(args.all), bool(args.resume)]
    if sum(chosen) != 1:
        parser.error("give exactly one of --invoice_path, --all or --resume")

    # A fresh clone has no catalogue, so seed it rather than failing on "no such table".
    inventory.ensure()

    # Checked before any work starts. Finding out after reading a file and opening a run that
    # the key was never set makes a configuration problem look like a bad invoice.
    llm.require_key()

    if args.resume:
        missing = [n for n in ("resolution", "actor", "justification")
                   if not getattr(args, n)]
        if missing:
            parser.error("--resume needs " + ", ".join("--" + m for m in missing))
        run_resume(args)
    elif args.all:
        run_all(args.all)
    else:
        run_one(args.invoice_path)


if __name__ == "__main__":
    try:
        main()
    except MissingKeyError as exc:
        # Not an invoice problem and not a crash to read a stack trace for. Say what to do.
        print(f"\n{exc}\n")
        raise SystemExit(2)
