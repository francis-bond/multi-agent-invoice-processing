"""Slice 1: read one invoice, ask Grok for structured fields, print the result."""

import argparse
from pathlib import Path

from langchain_core.runnables import Runnable

from llm import client
from models import ExtractedInvoice


EXTRACTION_PROMPT = """You are extracting data from a vendor invoice for an accounts payable system.

LABELS AND DATA ARE TREATED DIFFERENTLY.

Labels are the field names printed on the document. Interpret them liberally. Abbreviations,
misspellings, and unusual wording are expected, so read past them:
  "Vndr", "Vendor", "FROM", "Supplier", "Billed By"  -> vendor
  "Amt", "Total", "Amount Due", "Balance", "Total Amount"  -> total
  "Itms", "Items", "Line Items", "Products", "Description"  -> line items
  "Inv #", "INV NO", "Invoice Number", "Invoice No"  -> invoice number
  "Dt", "Date", "Issued"  -> issue date
  "Due Dt", "DUE", "Payment Due"  -> due date

Data is the values themselves. Transcribe them exactly as written. Do not correct spelling, expand
abbreviations, add missing prefixes, or tidy formatting:
  - Vendor written "Gadgets Co." stays "Gadgets Co.", not "Gadgets Company"
  - Invoice number written "1002" stays "1002", not "INV-1002"
  - An item written "SuperGizmo" stays "SuperGizmo", even if no such product plausibly exists
  - A negative quantity stays negative

MISSING VALUES. Return null, never an empty string. If the document states no currency, currency is
null. If there is no due date, due_date is null. Absent and empty are different things and are
handled differently downstream.

SUBTOTAL, TAX AND TOTAL each have two parts: the number, and source_text, the exact text you read it
from. If the document does not state a value at all, set BOTH to null. Do not put zero in a value
the document never mentions - a stated "Tax: $0.00" and an absent tax line are different facts, and
the citation is how they are told apart.

CHARGES THAT ARE NOT LINE ITEMS AND NOT TAX
Invoices carry amounts that are neither goods nor tax: shipping, freight, handling, delivery,
duties, insurance, late fees, deposits, discounts, credits, refunds, rounding adjustments.
Put every one of these in `charges`, with the label as written and the amount signed - a
discount or credit is negative.

Tax does NOT go in charges. It has its own field. Put it there and only there, however the
invoice labels it ("Sales Tax", "VAT", "GST", "Tax (6%)").

Goods do NOT go in charges. They are line items.

These amounts are the difference between subtotal plus tax and the stated total, so missing
one makes a correct invoice look like it cannot add up. If the total exceeds subtotal plus
tax, look for a charge before concluding anything.

DATES. Return ISO format (YYYY-MM-DD) when the date is unambiguous. If a date cannot be read
confidently, return it exactly as printed rather than guessing at the intended value.

REVISION AND NOTES. If the document declares itself a revision, amendment or correction - a
"revision" field, "R1", "revised", "supersedes", or similar - capture that marker. Capture any
free-text note or remark too. These decide whether a repeated invoice number is a duplicate bill
or a legitimate correction, so do not drop them.

CITATIONS. For every value, copy the exact text you read it from into source_text. This is checked
against the document afterwards, so it has to appear there verbatim. Do not tidy it, reformat it
or summarise it.

Include the label, not just the figure. "0.00" on its own is not a citation: it could have come
from anywhere in the document and proves nothing about where you read it. '"tax_amount": 0.00'
or "Tax (0%): $0.00" identifies the passage. Citations too short to identify a passage are
treated as missing.

In a structured document - XML, JSON - a line item is not written on one line: its name,
quantity and price sit in separate tags or keys. Copy the WHOLE containing element or object,
across however many lines it spans, exactly as written. "WidgetA 4 225.00" is a summary, not a
citation, and will not be found in the document.

{retry_note}
Invoice document:
---
{document}
---"""


RETRY_NOTE = """
YOUR LAST ATTEMPT DID NOT CHECK OUT
Every value you report carries the text you read it from, and those citations are verified
against the document afterwards. These did not appear in it:

{feedback}

A citation that is not in the document means either the figure is wrong or the text is. Read
those parts of the document again and copy what is actually written. Do not simply repeat your
previous answer, and do not invent a citation to satisfy the check - a value that genuinely is
not stated should be reported as absent.
"""


def grok() -> Runnable:
    """The real client. Separated so tests can substitute something else."""
    return client(ExtractedInvoice)


def extract(
    document_text: str,
    model: Runnable | None = None,
    feedback: str | None = None,
) -> tuple[ExtractedInvoice, str]:
    """Extract structured fields from invoice text.

    `model` is the seam. Production passes nothing and gets Grok. Tests pass a stand-in so the
    suite runs without an API key, without cost, and without the model's non-determinism making
    assertions impossible.

    With `feedback`, this is a second attempt carrying what was wrong with the first. Without
    it a retry is the same question to the same model at temperature zero, and returns the
    same answer.
    """
    model = model or grok()
    prompt = EXTRACTION_PROMPT.format(
        document=document_text,
        retry_note=RETRY_NOTE.format(feedback=feedback) if feedback else "",
    )
    return model.invoke(prompt), prompt


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract structured data from one invoice.")
    parser.add_argument("invoice_path", type=Path)
    args = parser.parse_args()

    document_text = args.invoice_path.read_text()
    result, _ = extract(document_text)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
