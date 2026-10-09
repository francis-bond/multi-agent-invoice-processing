"""The payments ledger. Consulted BEFORE paying, so it is a precondition, not a record."""

import pytest

from conftest import make_invoice
from ledger import fingerprint, prior_by_content, prior_by_number, record


@pytest.fixture
def ledger_db(tmp_path):
    """A ledger with no history, so a test never depends on what this machine has paid."""
    return tmp_path / "ledger.db"


class TestFingerprint:
    def test_identical_invoices_match(self):
        a = make_invoice(items=[("WidgetA", 2, 250.0)], total=500.0)
        b = make_invoice(items=[("WidgetA", 2, 250.0)], total=500.0, number="INV-OTHER")
        # Different number, same goods and money: that is the point of a content fingerprint.
        assert fingerprint(a) == fingerprint(b)

    def test_line_order_does_not_change_it(self):
        a = make_invoice(items=[("WidgetA", 1, 250.0), ("WidgetB", 1, 500.0)], total=750.0)
        b = make_invoice(items=[("WidgetB", 1, 500.0), ("WidgetA", 1, 250.0)], total=750.0)
        assert fingerprint(a) == fingerprint(b)

    def test_date_is_deliberately_excluded(self):
        """Two genuine invoices for identical goods on different dates are a plausible
        repeat order, so the date must not make them look like different invoices. The
        duplicate they produce is a warning for a person, not a hard block."""
        a = make_invoice(total=500.0)
        b = make_invoice(total=500.0)
        b.issue_date, b.due_date = "2099-12-31", "2099-12-31"
        assert fingerprint(a) == fingerprint(b)

    def test_different_money_does_not_match(self):
        a = make_invoice(items=[("WidgetA", 2, 250.0)], total=500.0)
        b = make_invoice(items=[("WidgetA", 2, 250.0)], total=600.0)
        assert fingerprint(a) != fingerprint(b)

    def test_different_vendor_does_not_match(self):
        a = make_invoice(total=500.0, vendor="Alpha")
        b = make_invoice(total=500.0, vendor="Beta")
        assert fingerprint(a) != fingerprint(b)


class TestPriorPayments:
    def test_nothing_is_found_in_an_empty_ledger(self, ledger_db):
        inv = make_invoice(total=500.0)
        assert prior_by_number(inv, ledger_db) == []
        assert prior_by_content(inv, ledger_db) == []

    def test_a_recorded_payment_is_found_by_number(self, ledger_db):
        inv = make_invoice(total=500.0)
        record("run-1", inv, ledger_db)
        prior = prior_by_number(inv, ledger_db)
        assert len(prior) == 1
        assert prior[0]["amount"] == 500.0

    def test_a_repeat_with_a_new_number_is_found_by_content(self, ledger_db):
        record("run-1", make_invoice(total=500.0, number="INV-1"), ledger_db)
        resubmitted = make_invoice(total=500.0, number="INV-2")
        assert prior_by_number(resubmitted, ledger_db) == []
        assert len(prior_by_content(resubmitted, ledger_db)) == 1
