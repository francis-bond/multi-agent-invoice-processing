"""The object that travels through the graph, accumulating as it goes.

Three categories live here, and keeping them distinct matters:

  1. What the document said            - extracted invoice data
  2. Evidence about how we read it     - source_text on each value, retry count
  3. What each node decided            - flags, routing, approval, payment result

Only the first comes from the invoice. The other two are the system's own record of its
reasoning, and they are what make the run auditable afterwards.
"""

from typing import Annotated, Literal, TypedDict

from models import ExtractedInvoice


class Flag(TypedDict):
    """A problem found during validation.

    Deliberately structured rather than a bare string. "why did this invoice get extra scrutiny?"
    has to be answerable from the record, and `code` lets routing match on it while `detail`
    explains it to a human.
    """

    code: str          # e.g. "quantity_exceeds_stock"
    detail: str        # e.g. "GadgetX: requested 20, available 5"
    severity: Literal["warning", "error"]


class InvoiceState(TypedDict, total=False):
    # --- identity -------------------------------------------------------------
    run_id: str                      # this processing run. Not the invoice number -
                                     # the same invoice can be processed more than once.
    source_path: str

    # --- what the document said ----------------------------------------------
    raw_text: str
    source_format: str  # which reader decoded the file, for triage
    invoice: ExtractedInvoice | None

    # --- evidence about extraction -------------------------------------------
    extraction_attempts: int
    extraction_feedback: str | None  # the specific complaint carried into a retry
    citation_problems: list          # citations that did not check out, for the log
    prompts: dict                    # node name -> prompt sent. For the run log, not for logic.

    # --- what each node decided ----------------------------------------------
    flags: Annotated[list[Flag], lambda a, b: a + b]   # accumulates across nodes
    needs_scrutiny: bool
    approval_decision: Literal["approve", "reject"] | None
    approval_reasoning: str | None
    approval_action: str | None        # what the agent says a person should do next

    # --- human intervention --------------------------------------------------
    # Set only on a re-run that a person asked for. An intervention supplies a better input;
    # the re-run still goes through every control.
    intervention_id: str | None
    supersedes_run: str | None          # the escalated run this one answers
    corrections: dict                   # field -> value, applied after extraction
    corrected_by: str | None            # whose correction, for the citation on a fixed figure
    corrections_applied: list           # what actually changed, for the log
    waived_findings: frozenset          # findings a named person accepted, scoped to theirs
    waiver: dict | None                 # who accepted them and why, shown to the agent

    # --- the approval critic loop --------------------------------------------
    critique_rounds: int              # revisions requested so far, not critiques run
    approval_feedback: str | None      # grounded objections carried into a revision
    critiques: Annotated[list[dict], lambda a, b: a + b]  # the full argument transcript
    escalation_reason: str | None      # set when the loop could not settle it
    payment_result: dict | None

    # --- how it ended ---------------------------------------------------------
    # Two different things, deliberately separate fields.
    #
    #   processing_error - the system failed to do its job. Unreadable file, extraction
    #                      exhausted its retries, API unavailable. The invoice may be
    #                      perfectly fine; we could not process it. A human should look
    #                      at the document.
    #
    #   rejection_reason - the system worked correctly and the answer is no. Invalid data,
    #                      item not stocked, approval declined. Nothing is broken, and it is
    #                      logged with the reasoning behind it.
    #
    # Collapsing these into one field would make "did our system break?" and "is this
    # invoice bad?" indistinguishable in the logs, which are the two questions anyone
    # debugging a failed run actually asks.
    processing_error: str | None
    rejection_reason: str | None
