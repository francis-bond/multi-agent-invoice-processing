"""CLI entry point: python main.py --invoice_path=data/invoices/invoice_1001.txt"""

import argparse
import sys

sys.path.insert(0, "src")

from graph import process  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Process one invoice end to end.")
    parser.add_argument("--invoice_path", required=True)
    args = parser.parse_args()

    result = process(args.invoice_path)

    if result.get("processing_error"):
        print(f"FAILED: {result['processing_error']}")
        raise SystemExit(1)

    inv = result["invoice"]
    print(f"run {result['run_id']}  {args.invoice_path}")
    print(f"  {inv.invoice_number}  {inv.vendor}  total={inv.total.value}")
    for li in inv.line_items:
        print(f"    {li.item} x{li.quantity} @ {li.unit_price}")

    flags = result.get("flags", [])
    if not flags:
        print("  validation: clean")
    else:
        print(f"  validation: {len(flags)} flag(s)")
        for f in flags:
            print(f"    [{f['severity']:7}] {f['code']:24} {f['detail']}")

    print(f"  scrutiny: {'yes' if result.get('needs_scrutiny') else 'no'}")
    if result.get("approval_decision"):
        print(f"  decision: {result['approval_decision']}")
        print(f"    {result['approval_reasoning']}")
    if result.get("rejection_reason"):
        print(f"  NOT PAID: {result['rejection_reason']}")
    elif result.get("payment_result"):
        print(f"  paid: {result['payment_result']['status']}")


if __name__ == "__main__":
    main()
