"""The gate, and the mock payment call.

The gate is three assertions, not a second approval. It re-examines nothing and re-decides
nothing - it refuses to let certain conditions reach a payment, whatever the approval agent
concluded. Everything upstream that makes a decision is an LLM, and LLMs can be wrong. This
is the only deterministic thing between a model's opinion and money leaving an account.

Kept deliberately narrow. A gate that blocks everything approval approved would make approval
pointless. These are conditions that are never legitimate, not conditions that are worrying.
"""

from ledger import prior_by_number, record
from models import ExtractedInvoice
from state import Flag

CENT = 0.01


def gate(inv: ExtractedInvoice, flags: list[Flag], decision: str) -> str | None:
    """Return a reason to block, or None to let the payment proceed."""

    if decision != "approve":
        return f"not approved (decision was {decision!r})"

    if not inv.total.was_stated:
        return "no total stated on the invoice; nothing to pay"

    if inv.total.value <= 0:
        return f"total is {inv.total.value}; payments must be positive"

    if any(li.quantity < 0 for li in inv.line_items):
        return "a line item has a negative quantity"

    # Last line of defence. Everything upstream that decides is an LLM; this is deterministic
    # and consults the ledger rather than trusting that nothing earlier missed it.
    already = prior_by_number(inv)
    if already:
        p = already[0]
        return (f"{inv.invoice_number} was already paid {p['amount']:,.2f} on "
                f"{p['paid_at'][:10]}; refusing to pay it twice")

    # The amount paid must equal the amount approved. Guards against a value drifting
    # between the decision and the transfer.
    computed = sum(li.quantity * li.unit_price for li in inv.line_items)
    if inv.subtotal.was_stated and abs(computed - inv.subtotal.value) > CENT:
        return (f"line items sum to {computed:.2f} but subtotal states "
                f"{inv.subtotal.value:.2f}; refusing to pay an unreconciled invoice")

    return None


def mock_payment(run_id: str, inv: ExtractedInvoice) -> dict:
    """Stands in for the banking API. The brief specifies simulating this locally.

    Records to the ledger immediately. If this were a real transfer the write would have to
    happen before the call, not after, so a crash mid-flight could not lose the record of a
    payment that had already gone out.
    """
    amount = inv.total.value
    vendor = inv.vendor or "(unknown vendor)"
    record(run_id, inv)
    print(f"  >> PAID {amount:,.2f} to {vendor}")
    return {"status": "success", "vendor": vendor, "amount": amount}
