"""Structured output schema for invoice extraction.

Every extracted value carries the text it was read from. That citation does two jobs:

  1. Distinguishes "the extractor misread this" from "the invoice is genuinely wrong" when
     validation fails. A quote that is not in the document means a misread, and retrying is
     worth it. A quote that checks out means the invoice is wrong, and retrying never helps.

  2. Distinguishes a stated zero from an absent value. `invoice_1001` states "Tax (0%): $0.00",
     a real zero. `invoice_1003` states no subtotal at all. The numbers are indistinguishable;
     the presence or absence of a citation is not.

Fields are deliberately REQUIRED even where the value may be null. Making them optional was
tried and made extraction measurably worse: on invoice_1002, where labels are abbreviated and
misspelled, the model simply omitted vendor, invoice number and both dates rather than work
for them. Required forces an attempt. The validators below clean up what comes back.
"""

from pydantic import BaseModel, Field, field_validator


class Cited(BaseModel):
    """A value plus the text it came from.

    `source_text` is null when the document does not state the value at all - which is a
    different thing from the value being zero or empty.
    """

    value: float | None = Field(description="The number as written, or null if not stated")
    source_text: str | None = Field(
        description="Exact text this was read from, copied verbatim. Null if the document "
                    "does not state this value anywhere."
    )

    @field_validator("source_text", mode="after")
    @classmethod
    def blank_is_absent(cls, v: str | None) -> str | None:
        return v.strip() if v and v.strip() else None

    @property
    def was_stated(self) -> bool:
        """True only if the document actually said this. A value with no citation was invented."""
        return self.source_text is not None


class LineItem(BaseModel):
    item: str = Field(description="Item name exactly as written on the invoice, not corrected")
    quantity: int = Field(description="Quantity ordered. Negative if the invoice says negative.")
    unit_price: float = Field(description="Price per unit in the invoice's currency")
    note: str | None = Field(
        description="Any note or annotation attached to this line, copied as written - "
                    "'Volume discount', 'Expedited', 'Sample', 'Replacement'. Null if the "
                    "line carries none. Do not invent one."
    )
    source_text: str = Field(
        description="The exact line this item was read from, copied verbatim"
    )


class Charge(BaseModel):
    """A charge or credit on the invoice that is not a line item and is not tax.

    Shipping, freight, handling, duties, discounts, credits, deposits, late fees. These are
    real money on the invoice and they are the difference between subtotal plus tax and the
    stated total. Without a field for them, every invoice carrying one reports a false
    total_mismatch - which is exactly what invoice_1010 did.

    Amounts are signed: a discount or credit is negative, so the arithmetic is one sum rather
    than a special case per label.
    """

    label: str = Field(description="The charge as labelled on the invoice, e.g. 'Shipping'")
    amount: float = Field(
        description="The amount, negative for a discount, credit or refund"
    )
    source_text: str = Field(description="The exact line this was read from, copied verbatim")


class ExtractedInvoice(BaseModel):
    invoice_number: str | None = Field(description="Invoice number as written, e.g. INV-1001")
    vendor: str | None = Field(description="Vendor name as written, not corrected or expanded")
    issue_date: str | None = Field(description="Date the invoice was issued, ISO if unambiguous")
    due_date: str | None = Field(description="Date payment is due, ISO if unambiguous")
    currency: str | None = Field(description="Currency code, e.g. USD or EUR. Null if not stated.")
    revision: str | None = Field(
        description="Revision marker if the document declares itself a revision, e.g. 'R1'. "
                    "Null if absent."
    )
    notes: str | None = Field(
        description="Any free-text note, comment or remark on the document. Null if absent."
    )
    line_items: list[LineItem]
    charges: list[Charge] = Field(description="Every line item on the invoice, in order")
    subtotal: Cited = Field(description="Subtotal, with the text it was read from")
    tax_amount: Cited = Field(description="Tax amount, with the text it was read from")
    total: Cited = Field(description="Total amount, with the text it was read from")

    @field_validator("invoice_number", "vendor", "issue_date", "due_date", "currency",
                     "revision", "notes", mode="after")
    @classmethod
    def empty_string_is_missing(cls, v: str | None) -> str | None:
        """Absent and empty are different downstream. The model returns "" regardless of what
        the prompt asks for, because "" satisfies anyOf: [string, null]. A schema constrains
        shape, not value, so the coercion has to happen here."""
        if v is None:
            return None
        return v.strip() or None
