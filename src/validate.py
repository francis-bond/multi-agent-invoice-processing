"""Validation: four kinds of check, all deterministic.

No LLM here. Every check is a lookup, a computation, or a rule. See LEARNING_NOTES for why
the fuzzy-matching agent was cut.

Each check returns Flags rather than raising. An invoice with five problems should report
five problems, not stop at the first one - the person reading the log wants the whole picture.
"""

from models import ExtractedInvoice
from inventory import lookup
from state import Flag

# Money comparisons need a tolerance. Floats do not reconcile exactly, and invoices are
# rounded to cents anyway.
CENT = 0.01


def _existence_and_stock(inv: ExtractedInvoice) -> list[Flag]:
    flags: list[Flag] = []
    for li in inv.line_items:
        stock = lookup(li.item)
        if stock is None:
            flags.append(Flag(
                code="item_not_found",
                detail=f"{li.item!r} is not in inventory",
                severity="error",
            ))
        elif stock == 0:
            flags.append(Flag(
                code="item_out_of_stock",
                detail=f"{li.item!r} is stocked but has zero on hand",
                severity="error",
            ))
        elif li.quantity > stock:
            flags.append(Flag(
                code="quantity_exceeds_stock",
                detail=f"{li.item}: requested {li.quantity}, available {stock}",
                severity="error",
            ))
    return flags


def _arithmetic(inv: ExtractedInvoice) -> list[Flag]:
    flags: list[Flag] = []
    computed = sum(li.quantity * li.unit_price for li in inv.line_items)

    # Only compare against values the document actually stated. An uncited value was never
    # on the invoice, so there is nothing to reconcile against and claiming a mismatch would
    # be inventing a problem.
    if inv.subtotal.was_stated and abs(computed - inv.subtotal.value) > CENT:
        flags.append(Flag(
            code="subtotal_mismatch",
            detail=f"line items sum to {computed:.2f}, invoice states subtotal {inv.subtotal.value:.2f}",
            severity="error",
        ))

    if inv.total.was_stated:
        base = inv.subtotal.value if inv.subtotal.was_stated else computed
        tax = inv.tax_amount.value if inv.tax_amount.was_stated else 0
        expected = base + tax
        if abs(expected - inv.total.value) > CENT:
            flags.append(Flag(
                code="total_mismatch",
                detail=f"subtotal plus tax is {expected:.2f}, invoice states total {inv.total.value:.2f}",
                severity="error",
            ))
    return flags


def _sanity(inv: ExtractedInvoice) -> list[Flag]:
    """Values that should not exist, regardless of whether they are internally consistent.

    INV-1009 is why this exists: its arithmetic reconciles exactly and it is still nonsense.
    """
    flags: list[Flag] = []

    for li in inv.line_items:
        if li.quantity < 0:
            flags.append(Flag(
                code="negative_quantity",
                detail=f"{li.item}: quantity {li.quantity}",
                severity="error",
            ))
        elif li.quantity == 0:
            flags.append(Flag(
                code="zero_quantity",
                detail=f"{li.item}: quantity 0",
                severity="warning",
            ))
        if li.unit_price < 0:
            flags.append(Flag(
                code="negative_unit_price",
                detail=f"{li.item}: unit price {li.unit_price}",
                severity="error",
            ))

    if inv.total.was_stated and inv.total.value < 0:
        flags.append(Flag(code="negative_total", detail=f"total {inv.total.value}", severity="error"))

    if not inv.vendor:
        flags.append(Flag(code="missing_vendor", detail="no vendor named", severity="error"))
    if not inv.line_items:
        flags.append(Flag(code="no_line_items", detail="invoice has no line items", severity="error"))
    if not inv.due_date:
        flags.append(Flag(code="missing_due_date", detail="no due date", severity="warning"))
    if not inv.invoice_number:
        flags.append(Flag(code="missing_invoice_number", detail="no invoice number", severity="warning"))

    return flags


def validate(inv: ExtractedInvoice) -> list[Flag]:
    return _existence_and_stock(inv) + _arithmetic(inv) + _sanity(inv)
