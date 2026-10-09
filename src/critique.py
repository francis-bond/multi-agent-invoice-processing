"""The approval critic.

A critic that sees what the approver saw, and is asked whether it agrees, says yes. Same
model, same evidence, temperature zero - agreement is the cheap answer and the loop becomes
theatre that costs latency and looks like diligence.

Three things make this one bite:

1. **It sees more.** The approval agent only ever receives extracted fields. It is
   structurally blind to anything the schema failed to model. The critic reads the source
   document, so "the 150.00 gap is a shipping line with no field for it" is a finding only
   the critic can reach. That is a different kind of check, not a second opinion.

2. **It has a different job.** Not "do you agree" but "verify this against the document and
   state what you checked". The output schema demands the verification work, so concurring
   silently is not available.

3. **Code decides, not the critic.** Every objection must quote the document verbatim, and
   `ground()` checks the quote actually appears in it. An objection that cannot be grounded
   is recorded and discarded. The critic cannot force a revision by asserting something
   confidently; it has to point at text that exists. Same mechanism as the extraction
   citation check, for the same reason.
"""

import os
import re

from langchain_core.runnables import Runnable
from langchain_xai import ChatXAI
from pydantic import BaseModel, Field

from models import ExtractedInvoice
from state import Flag

# How many times the approver may be sent back before the invoice goes to a person. Two
# informed revisions that have not resolved the objection mean the system cannot settle it.
MAX_ROUNDS = int(os.environ.get("CRITIQUE_MAX_ROUNDS", "2"))


class Objection(BaseModel):
    claim: str = Field(description="The specific statement in the reasoning being challenged")
    problem: str = Field(description="Why that statement is wrong or unsupported, in one or two sentences")
    quote: str = Field(
        description="Text copied VERBATIM from the invoice document that demonstrates the "
                    "problem. Must appear in the document character for character. An "
                    "objection whose quote cannot be found in the document is discarded."
    )


class Critique(BaseModel):
    verified: list[str] = Field(
        description="Each check you performed against the document and confirmed as correct. "
                    "One short line each. Name the figure or claim and what you compared it to."
    )
    objections: list[Objection] = Field(
        description="Problems found with the reasoning. Empty if the reasoning holds up."
    )


CRITIC_PROMPT = """You are auditing an approval decision made by a colleague on a vendor invoice.

Your colleague never saw the original document. They worked only from extracted fields and
automated validation findings. You have the document itself. Your job is to check their
reasoning against it.

This matters most where the extraction schema has no field for something. Our schema captures
line items, subtotal, tax and total. It does NOT capture shipping, freight, handling, duties,
discounts, credits, deposits, rounding adjustments or late fees. When a total does not
reconcile, a charge of that kind stated in the document is the most likely explanation, and
your colleague cannot see it. Look for one before accepting that the vendor made an error.

Also look for anything stated in the document that changes whether this should be paid and
that no extracted field carries: cancellation or void notices, "sample only" or "do not pay"
markings, duplicate or revision notices, a different payee, or payment terms already met.

The automated checks reconciled subtotal plus tax against the line items arithmetically and
correctly. Do not redo that arithmetic. Your question is whether something in the document
explains a discrepancy, not whether the sums were added up properly.

Report two things.

VERIFIED: every claim in the reasoning you checked against the document and found correct.
Be specific. "Total of 7,185.00 matches the document" is useful; "the reasoning is sound" is
not. If you checked nothing, say so plainly rather than padding this list.

OBJECTIONS: anything in the reasoning that is wrong or unsupported. Every objection must
quote the document verbatim, copied exactly, as the evidence. An objection you cannot
support with a quote from the document will be discarded, so do not raise one. Raise no
objections if the reasoning holds up - inventing one is worse than finding none.

THE DECISION UNDER REVIEW
  Decision:  {decision}
  Reasoning: {reasoning}

WHAT YOUR COLLEAGUE WAS SHOWN
  Vendor:   {vendor}
  Number:   {number}
  Total:    {total}
  Items:
{items}

  Validation findings:
{flags}

THE SOURCE DOCUMENT
--- begin document ---
{document}
--- end document ---
"""


def _llm() -> Runnable:
    llm = ChatXAI(
        model=os.environ.get("XAI_MODEL", "grok-4-1-fast"),
        api_key=os.environ["XAI_API_KEY"],
        temperature=0,
    )
    return llm.with_structured_output(Critique)


def _normalise(text: str) -> str:
    """Reduce a quote to the tokens that carry meaning, so honest reformatting still matches.

    Models reflow text when copying out of a document, and a byte-exact match would reject
    honest quotes - which would silently disable the whole grounding check rather than
    loosening it. That failure was real: a critic quoting three rows of a CSV joined them
    with commas where the document had newlines, and a verifiable quote was discarded as
    unsupported. Three of the sample invoices are CSV, so strict matching disabled the
    critic on a fifth of the corpus.

    Separators are therefore collapsed: whitespace, commas, colons, pipes and the like all
    become a single space. Word characters and decimal points survive, because the figures
    are the part that has to be right - "14750.00" must not be allowed to match "14750.99".

    The looser match does make a fabricated quote marginally easier to pass. That is the
    correct side to err on: a quote still has to reproduce the document's actual words and
    numbers in order, and the minimum length below stops a fragment matching anything.
    """
    return re.sub(r"[^\w.]+", " ", text).casefold().strip()


def ground(critique: Critique, document: str) -> tuple[list[Objection], list[Objection]]:
    """Split objections into those the document supports and those it does not.

    This is the control decision, and it is code. The critic supplies judgment and evidence;
    whether that evidence exists is a fact, and facts are not a model's to assert.
    """
    haystack = _normalise(document)
    supported, unsupported = [], []
    for obj in critique.objections:
        quote = _normalise(obj.quote)
        # A quote of two or three characters would match almost anything. Require enough
        # text to actually identify a passage.
        if len(quote) >= 8 and quote in haystack:
            supported.append(obj)
        else:
            unsupported.append(obj)
    return supported, unsupported


def as_feedback(objections: list[Objection]) -> str:
    """Turn grounded objections into the complaint carried back to the approval agent.

    An uninformed retry asks the same question and gets the same answer. The revision has to
    know what was wrong with the first attempt.
    """
    lines = []
    for i, o in enumerate(objections, 1):
        lines.append(
            f"{i}. You said: {o.claim}\n"
            f"   Problem: {o.problem}\n"
            f"   The document states: \"{o.quote.strip()}\""
        )
    return "\n".join(lines)


def critique(
    inv: ExtractedInvoice,
    flags: list[Flag],
    decision: str,
    reasoning: str,
    document: str,
    model: Runnable | None = None,
) -> tuple[Critique, str]:
    model = model or _llm()
    items = "\n".join(
        f"    {li.item} x{li.quantity} @ {li.unit_price}"
        + (f"   [note: {li.note}]" if li.note else "")
        for li in inv.line_items
    ) or "    (none)"
    findings = "\n".join(
        f"    [{f['severity']}] {f['code']}: {f['detail']}" for f in flags
    ) or "    None. All checks passed."
    prompt = CRITIC_PROMPT.format(
        decision=decision,
        reasoning=reasoning,
        vendor=inv.vendor or "(not stated)",
        number=inv.invoice_number or "(not stated)",
        total=inv.total.value if inv.total.was_stated else "(not stated)",
        items=items,
        flags=findings,
        document=document,
    )
    return model.invoke(prompt), prompt
