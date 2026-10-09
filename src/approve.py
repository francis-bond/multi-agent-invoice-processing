"""Routing and approval.

Routing is code. The agent never chooses its own lane - if it could decide whether a control
applies to it, the control is not a control. An auditor asking why a $47,000 invoice went
through gets a rule and a threshold, not a transcript of a model's opinion.

Approval is an agent, because "these three flags are all warnings on a small invoice from a
known vendor" is a judgment that does not reduce to a rule.
"""

from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from llm import client
from models import ExtractedInvoice
from policy import HOME_CURRENCY, SCRUTINY_THRESHOLD
from state import Flag


def needs_scrutiny(inv: ExtractedInvoice, flags: list[Flag]) -> bool:
    """Deterministic. Amount OR any flag at all.

    The flags matter as much as the amount: a $3,000 invoice with a negative quantity and no
    vendor deserves the careful path more than a clean $11,000 one does.
    """
    # The threshold is denominated in HOME_CURRENCY, so comparing a foreign total against it
    # compares different units. A foreign-currency invoice always takes the careful path
    # instead: we cannot tell whether it is above or below the limit.
    foreign = bool(inv.currency) and inv.currency.upper() != HOME_CURRENCY
    if foreign:
        return True
    over_threshold = inv.total.was_stated and inv.total.value > SCRUTINY_THRESHOLD
    return over_threshold or bool(flags)


class ApprovalDecision(BaseModel):
    decision: str = Field(description="Exactly 'approve' or 'reject'")
    reasoning: str = Field(
        description="Two or three sentences. Name the specific flags or amounts that drove "
                    "this, so a human reading the log understands the call without re-deriving it."
    )
    recommended_action: str = Field(
        description="One sentence on what a person should do next about THIS invoice, naming "
                    "the figure, item or party involved. Who to contact and about what. On an "
                    "approval say what to watch rather than inventing work."
    )


APPROVAL_PROMPT = """You are reviewing a vendor invoice on behalf of Acme Corp's accounts payable team.
Decide whether to approve it for payment or reject it.

Reject when the invoice should not be paid as submitted: items Acme does not stock, quantities
exceeding available inventory, impossible values, or figures that do not reconcile. These represent
real money leaving the company for goods that may not exist or were never ordered.

Approve when the invoice is sound, or when the only issues are immaterial to whether payment is owed.

DO NOT RECHECK THE ARITHMETIC. Every total on this invoice has already been reconciled
deterministically against the line items, including tax. If the figures did not add up, there would
be a subtotal_mismatch or total_mismatch finding below. The absence of one means they reconcile.
Recomputing the sums yourself will produce a wrong answer, because you will not account for tax the
way the checks did.

You are not deciding whether this invoice required review. That was already determined by policy
before it reached you. You are deciding whether it should be paid.

{scrutiny_note}
{waiver_note}
{revision_note}
INVOICE
  Vendor:   {vendor}
  Number:   {number}
  Due:      {due_date}
  Items:
{items}
  Other charges:
{extras}
  Subtotal: {subtotal}
  Tax:      {tax}
  Total:    {total} {currency}

VALIDATION FINDINGS
{flags}

Give a decision, the reasoning behind it, and what a person should do next.

Generic advice is not useful. "Contact the vendor" could be said of any invoice. Name the
amount, the item or the party: "ask Atlas Industrial to reissue with the 50.00 discrepancy
resolved" tells someone what to pick up."""

WAIVER_NOTE = """
A PERSON HAS ALREADY REVIEWED SOME OF THIS
{actor} examined this invoice and accepted the following findings as immaterial:
{waived}

Their reason: "{justification}"

That is a recorded decision by a named person who saw these findings, so treat those specific
ones as settled rather than re-arguing them. It does not extend to anything else: any finding
not in that list is still yours to weigh, and a problem they did not see is not covered by
their decision.
"""

REVISION_NOTE = """
YOUR PREVIOUS DECISION WAS CHALLENGED
A colleague audited it against the source document, which you have not seen. Each objection
below quotes that document, and the quote has been verified to appear in it. These are facts
about the document, not opinions.

{feedback}

Decide again. Where an objection is correct, change your decision or your reasoning to match
it. Where you still believe your original call was right, say explicitly why the objection
does not change it. Do not simply repeat your previous reasoning.
"""


def _llm() -> Runnable:
    return client(ApprovalDecision)


def approve(
    inv: ExtractedInvoice,
    flags: list[Flag],
    scrutiny: bool,
    model: Runnable | None = None,
    feedback: str | None = None,
    waiver: dict | None = None,
) -> tuple[ApprovalDecision, str]:
    """Decide whether to pay. With `feedback`, this is a revision rather than a first look.

    An uninformed retry asks the same question of the same model and gets the same answer.
    The revision has to carry the specific complaint, which is why feedback is a parameter
    rather than something the caller bakes into the invoice.
    """
    model = model or _llm()
    items = "\n".join(
        f"    {li.item} x{li.quantity} @ {li.unit_price}"
        + (f"   [note: {li.note}]" if li.note else "")
        for li in inv.line_items
    ) or "    (none)"
    extras = "\n".join(
        f"    {c.label}: {c.amount:,.2f}" for c in inv.charges
    ) or "    (none)"
    findings = "\n".join(
        f"  [{f['severity']}] {f['code']}: {f['detail']}" for f in flags
    ) or "  None. All checks passed."
    note = (
        "This invoice was routed for additional scrutiny by policy. Weigh the findings carefully."
        if scrutiny else
        "This invoice cleared the routing rules without flags."
    )
    prompt = APPROVAL_PROMPT.format(
        scrutiny_note=note,
        revision_note=REVISION_NOTE.format(feedback=feedback) if feedback else "",
        waiver_note=WAIVER_NOTE.format(
            actor=waiver["actor"],
            waived="\n".join(f"  - {c}" for c in sorted(waiver["waived"])),
            justification=waiver["justification"],
        ) if waiver and waiver.get("waived") else "",
        vendor=inv.vendor or "(not stated)",
        number=inv.invoice_number or "(not stated)",
        due_date=inv.due_date or "(not stated)",
        subtotal=inv.subtotal.value if inv.subtotal.was_stated else "(not stated)",
        tax=inv.tax_amount.value if inv.tax_amount.was_stated else "(not stated)",
        total=inv.total.value if inv.total.was_stated else "(not stated)",
        currency=inv.currency or f"({HOME_CURRENCY} assumed, none stated)",
        items=items,
        extras=extras,
        flags=findings,
    )
    return model.invoke(prompt), prompt
