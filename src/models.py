"""Structured output schema for invoice extraction.

Every extracted value carries the text it came from. That is what lets the system
distinguish "the extractor misread this" from "the invoice is genuinely wrong"
when validation fails later.
"""

from pydantic import BaseModel, Field, field_validator


class LineItem(BaseModel):
    item: str = Field(description="Item name exactly as written on the invoice, not corrected")
    quantity: int = Field(description="Quantity ordered. Negative if the invoice says negative.")
    unit_price: float = Field(description="Price per unit in the invoice's currency")
    source_text: str = Field(
        description="The exact line from the document this item was read from, copied verbatim"
    )


class ExtractedInvoice(BaseModel):
    """Note on required fields and empty strings.

    Fields are deliberately REQUIRED even though their values may be null. Making them optional was
    tried and made extraction measurably worse: on invoice_1002, where labels are abbreviated and
    misspelled, the model simply omitted vendor, invoice number, and both dates rather than work for
    them. Required forces an attempt; the validator below cleans up whatever comes back.

    Note on empty strings.

    The prompt asks for null on missing values and the schema types them as optional, but the model
    returns "" anyway: an empty string satisfies `anyOf: [string, null]`, so nothing in the contract
    forbids it. A prompt is a request and a schema constrains shape, not value. Only code run after
    the response guarantees anything, so the coercion lives here.
    """

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

    @field_validator("invoice_number", "vendor", "issue_date", "due_date", "currency",
                     "total_source_text", mode="after")
    @classmethod
    def empty_string_is_missing(cls, v: str | None) -> str | None:
        """Absent and empty are different things downstream. Normalise "" and "  " to None."""
        if v is None:
            return None
        return v.strip() or None
