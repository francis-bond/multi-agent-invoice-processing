"""Currency. The system pays in one, and refuses what it cannot price.

Converting would need a rate source, a rate date and a policy on who bears the spread. With
none of those, a conversion would be a fabricated number inside a payments record, so the
decision is to catch rather than convert.
"""

import pytest

from conftest import flag_codes, make_invoice
from ledger import fingerprint, prior_by_content, record
from payment import gate
from policy import HOME_CURRENCY
from validate import _currency


@pytest.fixture
def no_prior(monkeypatch):
    import payment
    monkeypatch.setattr(payment, "prior_by_number", lambda inv: [])


class TestValidation:
    def test_a_foreign_currency_is_flagged_as_an_error(self):
        """invoice_1014.xml states EUR. It was previously paid as a bare number."""
        flags = _currency(make_invoice(currency="EUR", total=4125.0))
        assert flag_codes(flags) == ["foreign_currency"]
        assert flags[0]["severity"] == "error"
        assert "EUR" in flags[0]["detail"]
        assert "exchange rate" in flags[0]["detail"]

    def test_the_home_currency_is_clean(self):
        assert _currency(make_invoice(currency=HOME_CURRENCY, total=100.0)) == []

    def test_case_does_not_matter(self):
        assert _currency(make_invoice(currency="usd", total=100.0)) == []

    def test_silence_is_taken_as_the_home_currency(self):
        """Most of the sample set states no currency. Treating silence as foreign would flag
        almost every invoice and the signal would be worthless."""
        assert _currency(make_invoice(currency=None, total=100.0)) == []


class TestTheGateRefusesToPayIt:
    def test_an_approved_foreign_invoice_is_still_not_paid(self, no_prior):
        inv = make_invoice(items=[("WidgetA", 2, 250.0)], subtotal=500.0, total=500.0,
                           currency="EUR")
        blocked = gate(inv, [], "approve")
        assert blocked and "EUR" in blocked
        assert "no exchange rate" in blocked

    def test_the_home_currency_passes(self, no_prior):
        inv = make_invoice(items=[("WidgetA", 2, 250.0)], subtotal=500.0, total=500.0,
                           currency=HOME_CURRENCY)
        assert gate(inv, [], "approve") is None


class TestRouting:
    def test_a_foreign_invoice_always_takes_the_scrutiny_path(self):
        """The threshold is denominated in HOME_CURRENCY, so comparing a foreign total
        against it compares different units. We cannot tell if it is over the limit."""
        from approve import needs_scrutiny
        small = make_invoice(total=1.0, currency="EUR")
        assert needs_scrutiny(small, []) is True

    def test_a_small_clean_home_currency_invoice_does_not(self):
        from approve import needs_scrutiny
        assert needs_scrutiny(make_invoice(total=1.0, currency=HOME_CURRENCY), []) is False


class TestTheLedger:
    def test_currency_is_part_of_an_invoices_identity(self):
        """Without it, 500 EUR and 500 USD for the same goods fingerprint identically and
        one is reported as a duplicate of the other. An amount is not money without a unit."""
        usd = make_invoice(items=[("WidgetA", 2, 250.0)], total=500.0, currency="USD")
        eur = make_invoice(items=[("WidgetA", 2, 250.0)], total=500.0, currency="EUR")
        assert fingerprint(usd) != fingerprint(eur)

    def test_a_payment_records_what_was_actually_paid(self, tmp_path):
        db = tmp_path / "ledger.db"
        record("run-1", make_invoice(total=500.0, currency="EUR"), db)
        import sqlite3
        conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT amount, currency FROM payments").fetchone()
        assert (row["amount"], row["currency"]) == (500.0, "EUR")

    def test_an_unstated_currency_is_recorded_as_the_home_one(self, tmp_path):
        """The assumption is written down rather than left as a null to be guessed at later."""
        db = tmp_path / "ledger.db"
        record("run-1", make_invoice(total=500.0, currency=None), db)
        import sqlite3
        conn = sqlite3.connect(db)
        assert conn.execute("SELECT currency FROM payments").fetchone()[0] == HOME_CURRENCY

    def test_cross_currency_invoices_are_not_duplicates_of_each_other(self, tmp_path):
        db = tmp_path / "ledger.db"
        record("run-1", make_invoice(total=500.0, currency="USD", number="INV-1"), db)
        eur = make_invoice(total=500.0, currency="EUR", number="INV-2")
        assert prior_by_content(eur, db) == []
