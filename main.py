"""CLI entry point: python main.py --invoice_path=data/invoices/invoice_1001.txt"""

import argparse
import sys

sys.path.insert(0, "src")

import inventory  # noqa: E402
from graph import process  # noqa: E402
from reasons import category, headline, remediation  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Process one invoice end to end.")
    parser.add_argument("--invoice_path", required=True)
    args = parser.parse_args()

    # A fresh clone has no catalogue, so seed it rather than failing on "no such table".
    inventory.ensure()

    result = process(args.invoice_path)

    if result.get("processing_error"):
        print(f"FAILED: {result['processing_error']}")
        raise SystemExit(1)

    inv = result["invoice"]
    unit = inv.currency or ""
    print(f"run {result['run_id']}  {args.invoice_path}  (read as {result.get('source_format')})")
    print(f"  {inv.invoice_number}  {inv.vendor}  total={inv.total.value} {unit}".rstrip())
    for li in inv.line_items:
        note = f"   [{li.note}]" if li.note else ""
        print(f"    {li.item} x{li.quantity} @ {li.unit_price}{note}")
    for c in inv.charges:
        print(f"    {c.label}: {c.amount:,.2f}")

    flags = result.get("flags", [])
    if not flags:
        print("  validation: clean")
    else:
        print(f"  validation: {len(flags)} flag(s)")
        for f in flags:
            print(f"    [{f['severity']:7}] {f['code']:24} {f['detail']}")

    print(f"  scrutiny: {'yes' if result.get('needs_scrutiny') else 'no'}")

    # The critic loop, where it ran. Each round is the approver being sent back with a
    # grounded objection, so the transcript is the interesting part of a contested invoice.
    for c in result.get("critiques", []):
        print(f"  critic round {c['round']}: reviewing {c['reviewed_decision']}, "
              f"{len(c['verified'])} verified, {len(c['grounded'])} grounded, "
              f"{len(c['discarded'])} discarded")
        for o in c["grounded"]:
            print(f"    objection: {o['problem']}")
            print(f"      quoted: {o['quote'].strip()!r}")
        for o in c["discarded"]:
            print(f"    discarded (quote not in document): {o['quote'].strip()[:60]!r}")

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

    outcome = ("paid" if result.get("payment_result")
               else "escalated" if result.get("escalation_reason") else "rejected")
    flags_d = [dict(f) for f in flags]
    print(f"  queue: {category(outcome, result.get('processing_error'), flags_d)}")
    print(f"  reason: {headline(outcome, result.get('rejection_reason'), None, flags_d, result.get('approval_decision'), result.get('escalation_reason'), result.get('critique_rounds'))}")

    if result.get("approval_action"):
        print(f"  next step: {result['approval_action']}")
    for name, advice in remediation(flags_d):
        print(f"    - {name}: {advice}")

    print("\n  Audit view of every run:  uv run python src/dashboard.py")


if __name__ == "__main__":
    main()
