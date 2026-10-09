"""Shared test helpers.

Two things make this suite worth running: it never calls a model, and it never touches the
real databases. Both would make tests slow, costly and dependent on whatever payment history
happened to be on disk.

The agent functions already take an injectable `model`, and the graph nodes are monkeypatched
where a whole run is exercised. What gets asserted is never the model's judgment - that is not
a testable property - but everything the surrounding code does with it.
"""

import pytest

from models import Charge, Cited, ExtractedInvoice, LineItem


def cited(value, text="stated"):
    """A figure the document stated. `was_stated` keys off the citation, not the value."""
    return Cited(value=value, source_text=text)


ABSENT = Cited(value=None, source_text=None)


def make_invoice(
    items=(("WidgetA", 2, 250.0),),
    subtotal=None,
    tax=None,
    total=None,
    charges=(),
    number="INV-TEST",
    vendor="Test Vendor",
    due_date="2026-02-01",
    revision=None,
    notes=None,
    currency="USD",
):
    """Build an invoice without going near a model.

    Line items are (item, quantity, unit_price) or (item, quantity, unit_price, note).
    Figures passed as None are absent from the document rather than zero - a distinction the
    validation depends on, so the helper has to preserve it.
    """
    return ExtractedInvoice(
        invoice_number=number,
        vendor=vendor,
        issue_date="2026-01-01",
        due_date=due_date,
        currency=currency,
        revision=revision,
        notes=notes,
        line_items=[
            LineItem(
                item=i[0], quantity=i[1], unit_price=i[2],
                note=i[3] if len(i) > 3 else None,
                source_text=f"{i[0]} x{i[1]} @ {i[2]}",
            )
            for i in items
        ],
        charges=[
            Charge(label=c[0], amount=c[1], source_text=f"{c[0]}: {c[1]}") for c in charges
        ],
        subtotal=cited(subtotal) if subtotal is not None else ABSENT,
        tax_amount=cited(tax) if tax is not None else ABSENT,
        total=cited(total) if total is not None else ABSENT,
    )


@pytest.fixture
def no_ledger(monkeypatch):
    """Isolate validation from real payment history.

    `validate` consults the ledger for duplicate detection, so without this a test's result
    would depend on which invoices happened to have been paid on this machine. Duplicate
    detection gets its own tests against a temporary ledger.
    """
    import validate
    monkeypatch.setattr(validate, "prior_by_number", lambda inv: [])
    monkeypatch.setattr(validate, "prior_by_content", lambda inv: [])


@pytest.fixture
def seeded_inventory():
    """The baseline catalogue: WidgetA 15, WidgetB 10, GadgetX 5, FakeItem 0."""
    import inventory
    inventory.setup()
    return inventory.DB_PATH


def flag_codes(flags):
    return [f["code"] for f in flags]
