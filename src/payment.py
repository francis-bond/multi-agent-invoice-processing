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
from policy import HOME_CURRENCY
from validate import source_of
from state import Flag

CENT = 0.01


def gate(
    inv: ExtractedInvoice,
    flags: list[Flag],
    decision: str,
    waived: frozenset[str] = frozenset(),
) -> str | None:
    """Return a reason to block, or None to let the payment proceed.

    `waived` is the set of findings a named person accepted, with a justification, in a
    recorded intervention. It is scoped to the findings that were actually in front of them:
    a problem appearing for the first time on a re-run was never seen by anyone and does not
    inherit someone else's approval. The absolutes below are never waivable at all, so this
    cannot be used to authorise paying twice.
    """

    if decision != "approve":
        return f"not approved (decision was {decision!r})"

    if not inv.total.was_stated:
        return "no total stated on the invoice; nothing to pay"

    # No exchange rate source exists here, so the amount owed in our currency is unknown. A
    # hardcoded rate would put a fabricated number into a payments record, and the rate date
    # and who bears the spread are treasury decisions, not ours to assume.
    if inv.currency and inv.currency.upper() != HOME_CURRENCY:
        return (f"invoice is in {inv.currency.upper()} and we pay in {HOME_CURRENCY}; "
                f"no exchange rate is configured, so a person has to price this")

    if inv.total.value <= 0:
        return f"total is {inv.total.value}; payments must be positive"

    if any(li.quantity < 0 for li in inv.line_items):
        return "a line item has a negative quantity"

    # Findings our own records produced, which an approval cannot waive.
    #
    # This used to be left to judgment, on the reasoning that whether an unknown item or an
    # odd price is material is a call the agent should make. That argument assumed the agent
    # fails independently and rarely. It does not: the critic argued a duplicate payment was
    # impossible, the approval agent agreed, and both were wrong together on a finding taken
    # straight from the ledger. The gate caught that one only because it happens to check the
    # ledger itself - the same argument applied to an unknown supplier or a tenfold overcharge
    # would have paid.
    #
    # So the rule is now the one the gate was built for: an invoice whose own records say the
    # goods, the price or the payee are wrong does not get paid on an agent's say-so. It goes
    # to a person. The document-derived findings stay the agent's to weigh, because it can see
    # everything they were based on.
    system_errors = [
        f for f in flags
        if f["severity"] == "error" and source_of(f["code"]) == "system"
        and f["code"] not in waived
    ]
    if system_errors:
        first = system_errors[0]
        more = f" (and {len(system_errors) - 1} more)" if len(system_errors) > 1 else ""
        return (f"our own records contradict this invoice: {first['detail']}{more}; "
                f"an approval cannot waive that")

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
    """Stands in for the banking API. Simulated locally.

    Records to the ledger immediately. If this were a real transfer the write would have to
    happen before the call, not after, so a crash mid-flight could not lose the record of a
    payment that had already gone out.
    """
    amount = inv.total.value
    vendor = inv.vendor or "(unknown vendor)"
    currency = (inv.currency or HOME_CURRENCY).upper()
    record(run_id, inv)
    print(f"  >> PAID {amount:,.2f} {currency} to {vendor}")
    return {"status": "success", "vendor": vendor, "amount": amount, "currency": currency}
