"""Plain-English reasons a run did not end in payment.

Flag codes are stable identifiers for machines. This module is the single place that turns
them into a sentence a human reads, so the dashboard and the CLI log viewer never describe
the same failure two different ways.

The goal is to say WHAT went wrong, not THAT something went wrong.
"""

# Short label per flag code. {field} style wording where the code names a specific field.
LABELS: dict[str, str] = {
    # duplicates and supersession - these decide payment on their own
    "duplicate_invoice_number": "Duplicate invoice number",
    "revises_paid_invoice": "Supersedes an invoice already paid",
    "possible_duplicate_billing": "Possible duplicate billing",
    # arithmetic
    "total_mismatch": "Math inconsistency: line items do not sum to the stated total",
    "subtotal_mismatch": "Math inconsistency: stated subtotal does not match the line items",
    # inventory
    "item_not_found": "Unknown item: not in the product catalogue",
    "item_out_of_stock": "Item out of stock",
    "quantity_exceeds_stock": "Quantity ordered exceeds stock on hand",
    "item_on_multiple_lines": "Same item billed on multiple lines",
    "item_matched_loosely": "Item matched only after ignoring a qualifier on its name",
    "critique_unsupported": "The critic raised an objection it could not evidence",
    "critic_unavailable": "The approval critic could not be run",
    # required fields
    "missing_vendor": "Missing required field: vendor",
    "missing_invoice_number": "Missing required field: invoice number",
    "missing_due_date": "Missing required field: due date",
    "no_line_items": "No line items on the invoice",
    # sanity
    "negative_quantity": "Invalid value: negative quantity",
    "zero_quantity": "Invalid value: quantity of zero",
    "negative_unit_price": "Invalid value: negative unit price",
    "negative_total": "Invalid value: negative total",
}

# Which finding an auditor should be told about first when several fired. A duplicate payment
# is a cash-out-the-door problem; a missing due date is paperwork.
PRIORITY = [
    "revises_paid_invoice",
    "duplicate_invoice_number",
    "possible_duplicate_billing",
    "negative_total",
    "negative_quantity",
    "negative_unit_price",
    "zero_quantity",
    "total_mismatch",
    "subtotal_mismatch",
    "quantity_exceeds_stock",
    "item_out_of_stock",
    "item_not_found",
    "item_on_multiple_lines",
    "item_matched_loosely",
    "critic_unavailable",
    "critique_unsupported",
    "no_line_items",
    "missing_vendor",
    "missing_invoice_number",
    "missing_due_date",
]


# Findings that mean a person has to do something, as distinct from findings that mean the
# vendor has to. A vendor arithmetic error is denied and finished: send it back, nothing
# happens internally. These are different - money has already moved wrongly, or the denial
# is one we caused and should review. They are the audit work queue.
ACTION_REQUIRED = {
    "revises_paid_invoice",      # we paid a version that has since been superseded
    "possible_duplicate_billing",  # same vendor, same total, different number: a judgment call
    "item_not_found",            # may be our catalogue, not their invoice
}


def category(
    outcome: str | None,
    processing_error: str | None,
    flags: list[dict] | None = None,
) -> str:
    """Bucket a run by what it asks of a human: 'paid', 'action', or 'denied'.

    'action' is the queue someone works through. A run lands there when it could not be
    processed at all, when it never finished, or when a finding implies an internal task
    rather than a message to the vendor.
    """
    if processing_error or outcome == "failed":
        return "action"
    if outcome == "escalated":
        return "action"  # the whole point of escalating is that a person has to decide
    if outcome is None:
        return "action"  # died mid-flight: the log exists but the run never concluded
    codes = {f["code"] for f in (flags or [])}
    if codes & ACTION_REQUIRED:
        return "action"
    return "paid" if outcome == "paid" else "denied"


def label(code: str) -> str:
    """Human phrase for a flag code. Unknown codes degrade to a readable form of the code."""
    return LABELS.get(code, code.replace("_", " ").capitalize())


def rank(code: str) -> int:
    return PRIORITY.index(code) if code in PRIORITY else len(PRIORITY)


def headline(
    outcome: str | None,
    blocked_reason: str | None,
    processing_error: str | None,
    flags: list[dict] | None = None,
    decision: str | None = None,
    escalation_reason: str | None = None,
) -> str:
    """One line saying why this run ended the way it did.

    Reads in the order the pipeline actually decides: a crash beats everything, then the
    payment gate's own refusal, then the worst validation finding.
    """
    if processing_error:
        return f"Could not process the file: {_first_clause(processing_error)}"

    if outcome == "escalated" and escalation_reason:
        return f"Unsettled after review: {_first_clause(escalation_reason)}"

    errors = [f for f in (flags or []) if f.get("severity") == "error"]
    warnings = [f for f in (flags or []) if f.get("severity") != "error"]
    candidates = errors or warnings

    # If anything put this run in the human work queue, lead with that, even when a
    # higher-ranked finding also fired. The reason has to explain the bucket, otherwise a
    # row reads "math inconsistency" while sitting under "needs a person" and the two
    # halves of the same table disagree.
    queued = [f for f in candidates if f["code"] in ACTION_REQUIRED]
    worst = min(queued or candidates, key=lambda f: rank(f["code"]), default=None)

    if outcome == "paid":
        if worst:
            return f"Paid with a warning: {label(worst['code'])}"
        return "Clean: no findings"

    extra = _and_others(len(flags or []))

    if blocked_reason:
        # Worth flagging loudly when the deterministic gate refused something the review
        # agent had approved: that is code catching a judgment call, and an auditor on a
        # duplicate-payment question needs to see it without opening the run.
        overrode = " - gate overrode the approval" if decision == "approve" else ""
        if worst and rank(worst["code"]) < len(PRIORITY):
            return f"{label(worst['code'])}{extra}{overrode}"
        return f"{_first_clause(blocked_reason)}{overrode}"

    if worst:
        return f"{label(worst['code'])}{extra}"

    if outcome is None:
        return "Run did not finish - no outcome recorded"
    return "Denied by approval review"


def _and_others(total: int) -> str:
    """Say how many other findings there were, so a one-line reason never hides a pile."""
    n = total - 1
    if n < 1:
        return ""
    return f" (+{n} other finding{'s' if n > 1 else ''})"


def _first_clause(text: str) -> str:
    """Trim a long reason to its first sentence so it fits on one line."""
    text = " ".join(text.split())
    for stop in (". ", "; "):
        if stop in text:
            text = text.split(stop)[0]
            break
    return text if len(text) <= 120 else text[:117] + "..."
