"""Routing and approval.

Routing is code. The agent never chooses its own lane - if it could decide whether a control
applies to it, the control is not a control. An auditor asking why a $47,000 invoice went
through gets a rule and a threshold, not a transcript of a model's opinion.

Approval is an agent, because "these three flags are all warnings on a small invoice from a
known vendor" is a judgment that does not reduce to a rule.
"""

import os

from langchain_core.runnables import Runnable
from langchain_xai import ChatXAI
from pydantic import BaseModel, Field

from models import ExtractedInvoice
from state import Flag

SCRUTINY_THRESHOLD = float(os.environ.get("SCRUTINY_THRESHOLD", "10000"))


def needs_scrutiny(inv: ExtractedInvoice, flags: list[Flag]) -> bool:
    """Deterministic. Amount OR any flag at all.

    The flags matter as much as the amount: a $3,000 invoice with a negative quantity and no
    vendor deserves the careful path more than a clean $11,000 one does.
    """
    over_threshold = inv.total.was_stated and inv.total.value > SCRUTINY_THRESHOLD
    return over_threshold or bool(flags)


class ApprovalDecision(BaseModel):
    decision: str = Field(description="Exactly 'approve' or 'reject'")
    reasoning: str = Field(
        description="Two or three sentences. Name the specific flags or amounts that drove "
                    "this, so a human reading the log understands the call without re-deriving it."
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

INVOICE
  Vendor:   {vendor}
  Number:   {number}
  Due:      {due_date}
  Items:
{items}
  Subtotal: {subtotal}
  Tax:      {tax}
  Total:    {total}

VALIDATION FINDINGS
{flags}

Give a decision and the reasoning behind it."""


def _llm() -> Runnable:
    llm = ChatXAI(
        model=os.environ.get("XAI_MODEL", "grok-4-1-fast"),
        api_key=os.environ["XAI_API_KEY"],
        temperature=0,
    )
    return llm.with_structured_output(ApprovalDecision)


def approve(
    inv: ExtractedInvoice,
    flags: list[Flag],
    scrutiny: bool,
    model: Runnable | None = None,
) -> tuple[ApprovalDecision, str]:
    model = model or _llm()
    items = "\n".join(
        f"    {li.item} x{li.quantity} @ {li.unit_price}" for li in inv.line_items
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
        vendor=inv.vendor or "(not stated)",
        number=inv.invoice_number or "(not stated)",
        due_date=inv.due_date or "(not stated)",
        subtotal=inv.subtotal.value if inv.subtotal.was_stated else "(not stated)",
        tax=inv.tax_amount.value if inv.tax_amount.was_stated else "(not stated)",
        total=inv.total.value if inv.total.was_stated else "(not stated)",
        items=items,
        flags=findings,
    )
    return model.invoke(prompt), prompt
