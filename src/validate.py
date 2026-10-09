"""Validation: four kinds of check, all deterministic.

No LLM here. Every check is a lookup, a computation, or a rule. See LEARNING_NOTES for why
the fuzzy-matching agent was cut.

Each check returns Flags rather than raising. An invoice with five problems should report
five problems, not stop at the first one - the person reading the log wants the whole picture.
"""

from models import ExtractedInvoice
from policy import HOME_CURRENCY, PRICE_TOLERANCE
from inventory import canonical, resolve, strip_annotation, vendor_is_approved
from ledger import prior_by_content, prior_by_number
from state import Flag

# Money comparisons need a tolerance. Floats do not reconcile exactly, and invoices are
# rounded to cents anyway.
CENT = 0.01


def strip_annotation_diff(raw: str) -> str:
    """The part of a line item's name that had to be ignored to match the catalogue."""
    return raw[len(strip_annotation(raw)):].strip()


def _existence_and_stock(inv: ExtractedInvoice) -> list[Flag]:
    """Existence is per item. Stock is per item AGGREGATED ACROSS LINES.

    invoice_1013 is why. It lists WidgetA three times - 15, then 5 at a volume discount,
    then 2 as a replacement. Each line passes a per-line check against stock of 15. Together
    they ask for 22. Checking lines individually misses every stock violation on an invoice
    that splits its order across lines, which is both a common billing pattern and an obvious
    way to slip an oversized order past a naive check.

    Lines are grouped by resolved catalogue identity rather than by the vendor's spelling,
    so "WidgetA" and "Widget A" on one invoice add up. Grouping on the raw string would let
    the same oversized order through simply by varying the spacing between lines.
    """
    flags: list[Flag] = []

    groups: dict[str, dict] = {}
    for li in inv.line_items:
        match = resolve(li.item)
        # Unmatched items still need a stable key, so fall back to their own canonical form:
        # two lines of "Widget C" and "widgetc" are one unknown product, not two.
        key = match.item or canonical(li.item)
        g = groups.setdefault(
            key, {"match": match, "qty": 0, "lines": 0, "written": [], "loose": []})
        g["qty"] += li.quantity
        g["lines"] += 1
        if li.item not in g["written"]:
            g["written"].append(li.item)
        # Looseness is a property of the line, not the group. invoice_1010 lists both
        # "WidgetA" and "WidgetA (rush order)": keeping only the first line's result would
        # throw away the fact that the second one needed a qualifier ignored.
        if match.is_loose and li.item not in g["loose"]:
            g["loose"].append(li.item)
        # Prefer the cleanest match in the group for the stock figure itself.
        if not g["match"].found or (g["match"].is_loose and not match.is_loose):
            g["match"] = match

    for g in groups.values():
        match, qty, lines = g["match"], g["qty"], g["lines"]
        # Always report the invoice's own wording. The audit trail has to show what the
        # vendor actually wrote, not what we matched it to.
        written = " / ".join(repr(w) for w in g["written"])
        name = match.item or g["written"][0]

        if not match.found:
            flags.append(Flag(
                code="item_not_found",
                detail=f"{written} is not in inventory",
                severity="error",
            ))
            continue

        for raw in g["loose"]:
            # Matching needed part of the name thrown away, and the discarded part can carry
            # a price implication - a rush order is not necessarily the same deal as a stock
            # one. Match it, pay attention to it, do not silently treat it as clean.
            discarded = strip_annotation_diff(raw)
            flags.append(Flag(
                code="item_matched_loosely",
                detail=(f"{raw!r} matched catalogue item {name!r} after ignoring "
                        f"{discarded!r}; confirm the qualifier carries no price change"),
                severity="warning",
            ))

        if match.stock == 0:
            flags.append(Flag(
                code="item_out_of_stock",
                detail=f"{name!r} is stocked but has zero on hand",
                severity="error",
            ))
        elif qty > match.stock:
            across = f" across {lines} lines" if lines > 1 else ""
            flags.append(Flag(
                code="quantity_exceeds_stock",
                detail=f"{name}: requested {qty}{across}, available {match.stock}",
                severity="error",
            ))

        # Not an error on its own, but the approval agent should know. Splitting one item
        # over several lines is normal for discounts and expedites, and is also how an
        # oversized order gets made to look small.
        if lines > 1:
            spellings = f" (as {written})" if len(g["written"]) > 1 else ""
            flags.append(Flag(
                code="item_on_multiple_lines",
                detail=f"{name} appears on {lines} separate lines{spellings} totalling {qty}",
                severity="warning",
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
        # Shipping, handling, discounts and the like are real money on the invoice. Leaving
        # them out of the expected total reported a false mismatch on every invoice that
        # carried one, and left the approval critic to rediscover the charge from the raw
        # document on each run. The schema should model the data.
        extra = sum(c.amount for c in inv.charges)
        expected = base + tax + extra
        if abs(expected - inv.total.value) > CENT:
            parts = f"subtotal plus tax{' plus charges' if inv.charges else ''}"
            detail = f"{parts} is {expected:.2f}, invoice states total {inv.total.value:.2f}"
            if inv.charges:
                detail += " (charges: " + ", ".join(
                    f"{c.label} {c.amount:,.2f}" for c in inv.charges) + ")"
            flags.append(Flag(code="total_mismatch", detail=detail, severity="error"))
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


def _pricing(inv: ExtractedInvoice) -> list[Flag]:
    """Is each line billed at something like the agreed price?

    This closes the largest hole the system had. Nothing else notices a unit price: arithmetic
    only checks that the figures agree with each other, the stock check only cares about
    quantity, and the approval agent has no reference price to compare against. A WidgetA
    billed at 2,500.00 instead of 250.00 is internally consistent, within stock, from a known
    vendor, and would have been paid.

    Only overcharges are flagged. A discount is the vendor's business and an invoice for less
    than the agreed price is not a risk to us. The tolerance exists because a premium for a
    rush or a short run is legitimate; what this catches is a price unrelated to the agreement.
    """
    flags: list[Flag] = []
    for li in inv.line_items:
        match = resolve(li.item)
        if not match.found or match.unit_price is None:
            continue  # an unknown item is already reported; there is no price to compare to
        ceiling = match.unit_price * (1 + PRICE_TOLERANCE)
        if li.unit_price > ceiling:
            over = (li.unit_price / match.unit_price - 1) * 100 if match.unit_price else 0
            flags.append(Flag(
                code="price_above_catalogue",
                detail=(f"{li.item!r} billed at {li.unit_price:,.2f} against an agreed "
                        f"{match.unit_price:,.2f} for {match.item} - {over:,.0f}% over "
                        f"({li.quantity} x {li.unit_price - match.unit_price:,.2f} = "
                        f"{li.quantity * (li.unit_price - match.unit_price):,.2f} more "
                        f"than agreed)"),
                severity="error",
            ))
    return flags


def _vendor(inv: ExtractedInvoice) -> list[Flag]:
    """Is the payee someone we have approved?

    Paying a counterparty nobody has approved is the failure an accounts payable control
    exists to prevent. Held for a person rather than refused outright, because the supplier
    list can be out of date and a new supplier is more likely than a fraudulent one - the
    same reasoning as an unknown item. Never silent, though.

    A missing vendor is already reported by the sanity checks, so this stays quiet about it
    rather than reporting the same absence twice.
    """
    if not inv.vendor or not inv.vendor.strip():
        return []
    if vendor_is_approved(inv.vendor):
        return []
    return [Flag(
        code="unknown_vendor",
        detail=(f"{inv.vendor!r} is not on the approved supplier list; confirm the "
                f"relationship before any payment is made"),
        severity="error",
    )]


def _currency(inv: ExtractedInvoice) -> list[Flag]:
    """An invoice in a currency we cannot pay.

    A stated currency other than ours is not a defect in the invoice - it is a gap in what
    this system can do. There is no exchange rate source here, so the amount owed cannot be
    determined, and a hardcoded rate would be a fabricated number in a payments record.

    An invoice that states no currency at all is taken as HOME_CURRENCY. Most of the sample
    set states none, and treating silence as foreign would flag almost everything.
    """
    if inv.currency and inv.currency.upper() != HOME_CURRENCY:
        return [Flag(
            code="foreign_currency",
            detail=(f"invoice is denominated in {inv.currency.upper()}, not "
                    f"{HOME_CURRENCY}; no exchange rate source is configured so the amount "
                    f"owed cannot be determined"),
            severity="error",
        )]
    return []


def _duplicates(inv: ExtractedInvoice) -> list[Flag]:
    """Has this already been paid, under this number or a different one?

    Neither case is an automatic rejection. A repeated invoice number with a revision marker
    is a legitimate correction superseding the earlier bill. Without a revision marker it is
    probably a duplicate bill. Telling those apart is judgment, so this flags and the approval
    agent decides.
    """
    flags: list[Flag] = []

    for prior in prior_by_number(inv):
        if inv.revision and inv.revision != prior["revision"]:
            flags.append(Flag(
                code="revises_paid_invoice",
                detail=(f"{inv.invoice_number} marked revision {inv.revision!r} was already paid "
                        f"{prior['amount']:,.2f} on {prior['paid_at'][:10]} "
                        f"(revision {prior['revision'] or 'none'}). This supersedes it; the earlier "
                        f"payment may need recovering."),
                severity="error",
            ))
        else:
            flags.append(Flag(
                code="duplicate_invoice_number",
                detail=(f"{inv.invoice_number} from {inv.vendor} was already paid "
                        f"{prior['amount']:,.2f} on {prior['paid_at'][:10]}"),
                severity="error",
            ))

    for prior in prior_by_content(inv):
        flags.append(Flag(
            code="possible_duplicate_billing",
            detail=(f"same vendor, amount and line items as {prior['invoice_number']} paid "
                    f"{prior['paid_at'][:10]}. This invoice is dated {inv.issue_date}, that one "
                    f"{prior['issue_date']}. Different numbers, identical content."),
            severity="error",
        ))

    return flags


def validate(inv: ExtractedInvoice) -> list[Flag]:
    return (_existence_and_stock(inv) + _pricing(inv) + _arithmetic(inv) + _sanity(inv)
            + _vendor(inv) + _currency(inv) + _duplicates(inv))
