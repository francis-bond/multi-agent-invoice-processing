"""Slice 1: read one invoice, ask Grok for structured fields, print the result."""

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_xai import ChatXAI

from models import ExtractedInvoice

load_dotenv()

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

DATES. Return ISO format (YYYY-MM-DD) when the date is unambiguous. If a date cannot be read
confidently, return it exactly as printed rather than guessing at the intended value.

CITATIONS. For every value, copy the exact text you read it from into source_text. This is checked
against the document later, so it must appear verbatim.

Invoice document:
---
{document}
---"""


def extract(document_text: str) -> ExtractedInvoice:
    llm = ChatXAI(
        model=os.environ.get("XAI_MODEL", "grok-4-1-fast"),
        api_key=os.environ["XAI_API_KEY"],
        temperature=0,
    )
    structured = llm.with_structured_output(ExtractedInvoice)
    return structured.invoke(EXTRACTION_PROMPT.format(document=document_text))


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract structured data from one invoice.")
    parser.add_argument("invoice_path", type=Path)
    args = parser.parse_args()

    document_text = args.invoice_path.read_text()
    result = extract(document_text)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
