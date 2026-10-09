"""Checking extracted values against the text they claim to come from.

Every figure the extractor reports carries the snippet it was read from. That citation is what
separates the two ways validation can fail, which need opposite responses:

- **the extractor misread the document.** The invoice is fine. Retrying can fix it.
- **the invoice's own figures are wrong.** Extraction was perfect. Retrying returns the same
  answer forever, and the right move is to refuse the invoice.

Without citations those look identical - a number that does not reconcile - and the system
either retries things that can never improve or rejects invoices it simply failed to read.

The test is whether the cited text actually appears in the document. A citation that does not
is either a misread or an invention, and both warrant another attempt. A citation that does
means the extractor read faithfully, so a figure that still does not reconcile is the vendor's
problem and no amount of retrying will change it.
"""

import re

from models import ExtractedInvoice
from policy import MAX_EXTRACTION_ATTEMPTS  # noqa: F401  (re-exported for callers)

# Enough text to identify a passage. A citation shorter than this is not evidence of anything:
# invoice_1009 was once cited as "0.00", which does appear in the document and says nothing
# about where the figure was read from. The prompt asks for the label alongside the figure for
# exactly this reason, and this is the check that makes the asking stick.
MIN_QUOTE = 6


def normalise(text: str) -> str:
    """Reduce text to the tokens that carry meaning, so honest reformatting still matches.

    Models reflow whitespace and padding when copying out of a document, and a byte-exact
    comparison would reject honest citations - which would silently turn this check off
    rather than tighten it. Separators collapse; word characters and decimal points survive,
    because the figures are exactly the part that has to be right. "14750.00" must never
    match "14750.99".

    One definition, used by both the extraction citation check and the critic's quote
    grounding. Two copies of this would drift, and the two halves of the system would then
    disagree about what counts as evidence.
    """
    return re.sub(r"[^\w.]+", " ", text).casefold().strip()


def appears_in(quote: str | None, document: str) -> bool:
    """Is this snippet really in the document?"""
    if not quote:
        return False
    needle = normalise(quote)
    return len(needle) >= MIN_QUOTE and needle in normalise(document)


def verify(inv: ExtractedInvoice, document: str) -> list[str]:
    """Complaints about citations that do not hold up, phrased for the extractor to act on.

    An empty list means every value was traceable to the document. It does not mean the
    invoice is correct - only that we read it faithfully, which is the distinction that makes
    retrying worthwhile or pointless.
    """
    problems: list[str] = []

    for name in ("subtotal", "tax_amount", "total"):
        figure = getattr(inv, name)
        if not figure.was_stated:
            continue  # absent from the document is a fact, not a misreading
        if not appears_in(figure.source_text, document):
            problems.append(
                f"You reported {name} as {figure.value} and cited "
                f"{figure.source_text!r} as the text you read it from, but that text does not "
                f"appear in the document. Either the figure is wrong or the citation is. "
                f"Find where {name} is actually stated, or report it as absent if it is not."
            )

    for i, li in enumerate(inv.line_items, 1):
        if not appears_in(li.source_text, document):
            problems.append(
                f"Line item {i} ({li.item} x{li.quantity} @ {li.unit_price}) cites "
                f"{li.source_text!r}, which does not appear in the document. Copy the line "
                f"exactly as it is written."
            )

    for c in inv.charges:
        if not appears_in(c.source_text, document):
            problems.append(
                f"The charge {c.label!r} of {c.amount} cites {c.source_text!r}, which does "
                f"not appear in the document. If no such charge is stated, do not report one."
            )

    return problems


def as_feedback(problems: list[str]) -> str:
    """The complaint carried into a retry.

    An uninformed retry asks the same question of the same model at the same temperature and
    gets the same answer. The only thing that makes a second attempt different is knowing
    what was wrong with the first.
    """
    return "\n".join(f"{i}. {p}" for i, p in enumerate(problems, 1))
