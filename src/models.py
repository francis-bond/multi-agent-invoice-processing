"""Structured output schema for invoice extraction.

Every extracted value carries the text it came from. That is what lets the system
distinguish "the extractor misread this" from "the invoice is genuinely wrong"
when validation fails later.
"""

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    item: str = Field(description="Item name exactly as written on the invoice, not corrected")
    quantity: int = Field(description="Quantity ordered. Negative if the invoice says negative.")
    unit_price: float = Field(description="Price per unit in the invoice's currency")
    source_text: str = Field(
        description="The exact line from the document this item was read from, copied verbatim"
    )


class ExtractedInvoice(BaseModel):
    invoice_number: str | None = Field(description="Invoice number as written, e.g. INV-1001")
    vendor: str | None = Field(description="Vendor name as written, not corrected or expanded")
    issue_date: str | None = Field(description="Date the invoice was issued, ISO format if parseable")
    due_date: str | None = Field(description="Date payment is due, ISO format if parseable")
    currency: str | None = Field(description="Currency code, e.g. USD or EUR. Null if not stated.")
    line_items: list[LineItem] = Field(description="Every line item on the invoice, in order")
    subtotal: float | None = Field(description="Subtotal as stated on the invoice")
    tax_amount: float | None = Field(description="Tax amount as stated on the invoice")
    total: float | None = Field(description="Total amount as stated on the invoice")
    total_source_text: str | None = Field(
        description="The exact line the total was read from, copied verbatim"
    )
