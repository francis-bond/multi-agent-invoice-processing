"""Slice 1: read one invoice, ask Grok for structured fields, print the result."""

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_xai import ChatXAI

from models import ExtractedInvoice

load_dotenv()

EXTRACTION_PROMPT = """You are extracting data from a vendor invoice for an accounts payable system.

Transcribe what the document says. Do not correct, normalise, or infer.

- If a value is missing, return null. Do not guess it.
- If a quantity is negative, report it as negative.
- Copy item names exactly as written, including misspellings.
- For every value you extract, copy the exact text you read it from into the source_text field.
  This is checked against the document later, so it must appear verbatim.

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
