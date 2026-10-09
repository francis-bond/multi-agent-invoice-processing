"""Read-only lookups the critic may call for itself.

The critic reads the invoice. Everything else it needs to check - what we have already paid,
what the catalogue says, who we have approved - lives in our own databases, and until it could
query them it was simply blind to all of it.

That blindness caused a real near miss. Validation flagged an invoice as already paid, from
the ledger. The critic could not see the ledger, so it reasoned from the only evidence it had:
the document said nothing about a duplicate, and the invoice date preceded the payment date.
Both true, neither relevant, and the approval agent was persuaded to approve paying 5,000.00 a
second time.

**These tools give the critic evidence, not authority.** Nothing here writes, nothing here
decides, and the critic's conclusions are still grounded by code and still subject to the
payment gate. The change is that an objection about our records can now be checked against our
records instead of inferred from the document's silence.

Each finding is answerable by exactly one of these, which is what makes the control
deterministic: an objection to a finding is only allowed if the lookup that bears on it was
actually performed.
"""

from langchain_core.tools import tool

import inventory
import ledger
from pathlib import Path

# Where invoices arrive. The pre-scan and this lookup both read it.
DATA_DIR = Path(__file__).parent.parent / "data" / "invoices"
# Which lookup bears on which finding. An objection about a system-derived finding has to be
# backed by the matching lookup, or it is still an argument from silence.
ANSWERED_BY: dict[str, str] = {
    "duplicate_invoice_number": "payment_history",
    "revises_paid_invoice": "payment_history",
    "possible_duplicate_billing": "payment_history",
    "item_not_found": "catalogue",
    "item_out_of_stock": "catalogue",
    "quantity_exceeds_stock": "catalogue",
    "item_matched_loosely": "catalogue",
    "price_above_catalogue": "catalogue",
    "unknown_vendor": "supplier",
    "superseded_by_sibling": "inbox",
    "sibling_totals_differ": "inbox",
    "sibling_file_same_number": "inbox",
    "supersedes_unprocessed_sibling": "inbox",
}


@tool
def payment_history(invoice_number: str) -> str:
    """Look up whether an invoice number has already been paid, and for how much.

    Use this before objecting to any finding about duplicates or revisions. The invoice
    document cannot tell you what we have paid; only this can.
    """
    prior = ledger.payments_for_number(invoice_number)
    if not prior:
        return f"No payment has been recorded against invoice number {invoice_number}."
    lines = [f"Invoice number {invoice_number} has {len(prior)} recorded payment(s):"]
    for p in prior:
        lines.append(f"  paid {p['amount']:,.2f} {p['currency'] or ''} to {p['vendor']} "
                     f"on {p['paid_at'][:10]} (revision {p['revision'] or 'none'})")
    return "\n".join(lines)


@tool
def catalogue(item: str) -> str:
    """Look up an item's stock level and agreed unit price in our catalogue.

    Use this before objecting to any finding about an unknown item, stock, or a price above
    the agreed one. The invoice cannot tell you what we stock or what we agreed to pay.
    """
    match = inventory.resolve(item)
    if not match.found:
        return (f"{item!r} is not in the catalogue under that name or any spelling variant "
                f"of it. We do not stock it.")
    price = f"{match.unit_price:,.2f}" if match.unit_price is not None else "not recorded"
    return (f"{item!r} matches catalogue item {match.item!r} (matched: {match.how}). "
            f"Stock on hand: {match.stock}. Agreed unit price: {price}.")


@tool
def supplier(name: str) -> str:
    """Look up whether a vendor is on our approved supplier list.

    Use this before objecting to a finding that a vendor is unapproved. The invoice cannot
    tell you who we have approved.
    """
    if inventory.vendor_is_approved(name):
        return f"{name!r} is on the approved supplier list."
    return (f"{name!r} is NOT on the approved supplier list. Nothing in the invoice can "
            f"change that; only someone adding them to it can.")


@tool
def inbox(invoice_number: str) -> str:
    """Look up other files in the inbox claiming the same invoice number.

    Use this before objecting to a finding about a revision or a sibling file. You were given
    one document; this tells you what else is sitting alongside it.
    """
    import inbox as inbox_mod

    number = "".join(c for c in invoice_number if c.isdigit())
    group = inbox_mod.scan(DATA_DIR).get(number, [])
    if not group:
        return (f"No files in the inbox claim invoice number {invoice_number}. Note that the "
                f"document you were given may itself not be in that directory.")
    lines = [f"{len(group)} file(s) in the inbox claim invoice number {invoice_number}:"]
    for g in group:
        total = f"{g['total']:,.2f}" if g["total"] is not None else "unreadable"
        rev = f", marks itself a revision ({g['revision']})" if g["revision"] else ""
        lines.append(f"  {g['name']}: stated total {total}{rev}")
    return "\n".join(lines)


TOOLS = [payment_history, catalogue, supplier, inbox]
BY_NAME = {t.name: t for t in TOOLS}


def required_lookup(finding_code: str) -> str | None:
    """The lookup that bears on a finding, or None if the document answers it."""
    return ANSWERED_BY.get(finding_code)
