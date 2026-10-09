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

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

import lookups
from citations import normalise as _normalise
from llm import chat, client
from policy import CRITIC_MAX_TOOL_STEPS, CRITIQUE_MAX_ROUNDS
from models import ExtractedInvoice
from state import Flag
from validate import source_of

# How many times the approver may be sent back before the invoice goes to a person. Two
# informed revisions that have not resolved the objection mean the system cannot settle it.
MAX_ROUNDS = CRITIQUE_MAX_ROUNDS
MAX_TOOL_STEPS = CRITIC_MAX_TOOL_STEPS


class Objection(BaseModel):
    targets: str = Field(
        description="The exact finding code this objection challenges, copied from the "
                    "findings list - for example 'total_mismatch'. Use 'reasoning' if you "
                    "are challenging how the decision was argued rather than a finding."
    )
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

WHAT YOU CAN AND CANNOT ARGUE WITH
Each finding below says where its facts came from.

A finding "from the document" was read off the invoice in front of you. You can see everything
it was based on, so challenge it freely - that is the point of your having the document.

A finding "from the system" was looked up in our own records: our payment history, our
catalogue, our approved supplier list. None of that is in the invoice. The document's silence
about it proves nothing - an invoice we have already paid does not announce that fact, a vendor
we have never approved does not say so, and a price above the one we agreed looks exactly like
a normal price.

You can check those for yourself. You have three lookups:

  payment_history(invoice_number)  what we have already paid against a number
  catalogue(item)                  stock on hand and the agreed unit price
  supplier(name)                   whether a vendor is on the approved list

CALL THE RELEVANT ONE BEFORE OBJECTING TO ANY FINDING FROM THE SYSTEM, and quote what it
returned as your evidence. An objection about our records that is not backed by the matching
lookup is discarded, because without it you are guessing. Never reason from dates in the
document about when we paid something - look it up instead. An invoice dated January being
paid in October is completely ordinary, not a contradiction.

Use them to confirm a finding as readily as to challenge one. "I looked up the catalogue and
WidgetC genuinely is not there" is a useful thing to report.

Name the finding you are challenging in `targets`, exactly as the code appears below, or
"reasoning" if your objection is about how the decision was argued rather than about a finding.

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
  Other charges:
{extras}
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
    return client(Critique)


def ground(
    critique: Critique,
    document: str,
    performed: list[dict] | None = None,
) -> tuple[list[Objection], list[Objection]]:
    """Split objections into those that stand and those that do not.

    This is the control decision, and it is code. The critic supplies judgment and evidence;
    whether that evidence exists, and whether it bears on the finding at all, are facts.

    Two independent reasons to discard an objection:

    1. **The quote is not in the document.** Then it is an assertion, not evidence.

    2. **The evidence comes from the wrong place.** A finding read off the document must be
       argued from the document. A finding taken from our own records must be argued from
       those records, which means the critic has to have actually called the lookup that
       bears on it and quoted what came back.

       This is the one that matters. A critic once argued that a duplicate payment was
       impossible because no duplicate notice appeared in the document and the invoice date
       preceded the payment date. Both true, neither relevant, and the approval agent was
       persuaded to approve paying 5,000.00 a second time. The argument was coherent and
       quoted the document accurately - it was simply about something the document cannot
       speak to. Now the critic can look the answer up, and an objection about our records
       only counts if it did.
    """
    from_document = _normalise(document)
    from_lookups = _normalise(" ".join(l["result"] for l in (performed or [])))
    consulted = {l["tool"] for l in (performed or [])}

    supported, unsupported = [], []
    for obj in critique.objections:
        quote = _normalise(obj.quote)
        # A quote of two or three characters would match almost anything. Require enough
        # text to actually identify a passage.
        if len(quote) < 8:
            unsupported.append(obj)
            continue

        if obj.targets == "reasoning" or source_of(obj.targets) == "document":
            # The document answers this finding, so the document is where the evidence has
            # to be. "reasoning" lands here too: an argument about how the decision was made
            # still has to point at something real.
            ok = quote in from_document
        else:
            # Our own records answer it. The critic has to have actually looked, and the
            # evidence has to come from what the lookup returned - otherwise this is the
            # argument from silence all over again, just with a tool available and unused.
            #
            # A finding nobody has classified has no lookup that bears on it, so there is no
            # way to evidence an objection to it and it is discarded. That fails safe: a new
            # check cannot be argued away until someone decides how it should be answered.
            needed = lookups.required_lookup(obj.targets)
            ok = bool(needed) and needed in consulted and quote in from_lookups

        (supported if ok else unsupported).append(obj)
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


def _gather(prompt: str, chat_model=None) -> list[dict]:
    """Let the critic look things up, and record every lookup it made.

    A plain tool-calling loop: ask, run whatever it asked for, hand back the results, repeat
    until it stops asking or runs out of steps. Only the lookups are returned, not the
    conversation - see `critique` for why the two phases are kept apart.

    `ground` needs to know what was actually consulted, because a tool that exists and was
    not called is no better than no tool at all.
    """
    model = chat_model or chat(tools=lookups.TOOLS)
    messages: list = [HumanMessage(prompt)]
    performed: list[dict] = []

    for _ in range(MAX_TOOL_STEPS):
        reply = model.invoke(messages)
        messages.append(reply)
        calls = getattr(reply, "tool_calls", None) or []
        if not calls:
            break
        for call in calls:
            fn = lookups.BY_NAME.get(call["name"])
            if fn is None:
                result = f"There is no lookup called {call['name']!r}."
            else:
                try:
                    result = fn.invoke(call["args"])
                except Exception as exc:
                    # A failed lookup is reported to the critic rather than raised. It can
                    # still write a critique; it just has one less piece of evidence.
                    result = f"That lookup failed: {exc}"
            performed.append({"tool": call["name"], "args": call["args"], "result": result})
            messages.append(ToolMessage(content=result, tool_call_id=call["id"]))
    return performed


FINALISE = """
WHAT YOUR LOOKUPS RETURNED
{results}

Now write your critique. Report every check you performed, the lookups among them, and raise
only objections you can evidence - from the document for a finding read off the document, and
from what a lookup returned for a finding from our records. Quote the evidence as it appears
above."""


def critique(
    inv: ExtractedInvoice,
    flags: list[Flag],
    decision: str,
    reasoning: str,
    document: str,
    model: Runnable | None = None,
    chat_model=None,
) -> tuple[Critique, str, list[dict]]:
    """Audit an approval decision. Returns the critique, the prompt, and the lookups made.

    Two phases, because tool calling and structured output want different things from the
    model: first it gathers whatever evidence it wants, then it writes the critique against a
    schema. The alternative is one call that can either use tools or return a schema but not
    reliably both.
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
    # Each finding is labelled with where its facts came from, because that decides whether
    # the critic has any standing to argue with it.
    findings = "\n".join(
        f"    [{f['severity']}] {f['code']} (from the {source_of(f['code'])}): {f['detail']}"
        for f in flags
    ) or "    None. All checks passed."
    prompt = CRITIC_PROMPT.format(
        decision=decision,
        reasoning=reasoning,
        vendor=inv.vendor or "(not stated)",
        number=inv.invoice_number or "(not stated)",
        total=inv.total.value if inv.total.was_stated else "(not stated)",
        items=items,
        extras=extras,
        flags=findings,
        document=document,
    )
    performed = _gather(prompt, chat_model)

    # Phase two gets the lookup results as text in a fresh prompt rather than the tool
    # conversation itself. Passing the tool messages through hung indefinitely: structured
    # output is implemented with function calling, so handing it a conversation already
    # mid-tool-use asks the model to emit a schema-tool inside an exchange about other tools,
    # and it simply stops responding. Separating the phases makes each one a thing the model
    # does well, and the full prompt is still what gets logged.
    results = "\n\n".join(
        f"  {p['tool']}({', '.join(f'{k}={v!r}' for k, v in p['args'].items())}):\n"
        + "\n".join(f"    {line}" for line in p["result"].splitlines())
        for p in performed
    ) or "  You made no lookups."
    final_prompt = prompt + FINALISE.format(results=results)
    return model.invoke(final_prompt), final_prompt, performed
