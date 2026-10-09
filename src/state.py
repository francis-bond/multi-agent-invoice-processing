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
    invoice: ExtractedInvoice | None

    # --- evidence about extraction -------------------------------------------
    extraction_attempts: int
    extraction_feedback: str | None  # the specific complaint carried into a retry
    prompts: dict                    # node name -> prompt sent. For the run log, not for logic.

    # --- what each node decided ----------------------------------------------
    flags: Annotated[list[Flag], lambda a, b: a + b]   # accumulates across nodes
    needs_scrutiny: bool
    approval_decision: Literal["approve", "reject"] | None
    approval_reasoning: str | None
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
    #                      item not stocked, approval declined. Nothing is broken. The brief
    #                      requires this be logged with reasoning.
    #
    # Collapsing these into one field would make "did our system break?" and "is this
    # invoice bad?" indistinguishable in the logs, which are the two questions anyone
    # debugging a failed run actually asks.
    processing_error: str | None
    rejection_reason: str | None
