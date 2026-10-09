"""The read-only lookups the critic may call, and the rule that it must call them.

These give the critic evidence, not authority. Nothing here writes, nothing here decides, and
the critic's conclusions are still grounded by code and still subject to the payment gate.
"""

import pytest

from conftest import make_invoice
from critique import Critique, Objection, ground
from ledger import record
from lookups import ANSWERED_BY, catalogue, payment_history, required_lookup, supplier
from validate import EVIDENCE_SOURCE

pytestmark = pytest.mark.usefixtures("seeded_inventory")

DOC = "INVOICE #INV-1\nVendor: Widgets Inc.\nWidgetA 2 @ $250.00\nTOTAL: $500.00\n"


class TestTheCatalogueLookup:
    def test_it_reports_stock_and_the_agreed_price(self):
        out = catalogue.invoke({"item": "WidgetA"})
        assert "15" in out and "250.00" in out

    def test_it_resolves_a_spelling_variant_and_says_so(self):
        out = catalogue.invoke({"item": "Widget A"})
        assert "WidgetA" in out and "normalized" in out

    def test_an_unknown_item_is_answered_plainly(self):
        out = catalogue.invoke({"item": "WidgetC"})
        assert "not in the catalogue" in out
        assert "variant" in out, "say that variants were tried, or it reads as a near miss"


class TestTheSupplierLookup:
    def test_an_approved_supplier(self):
        assert "is on the approved supplier list" in supplier.invoke({"name": "Widgets Inc."})

    def test_an_unapproved_one_closes_the_argument_off(self):
        """Phrased to pre-empt the failure this exists to stop: the critic deciding the
        document implies the vendor is fine."""
        out = supplier.invoke({"name": "Fraudster LLC"})
        assert "NOT on the approved supplier list" in out
        assert "Nothing in the invoice can change that" in out


class TestThePaymentHistoryLookup:
    def test_an_unpaid_number(self, tmp_path, monkeypatch):
        import ledger
        monkeypatch.setattr(ledger, "DB_PATH", tmp_path / "l.db")
        assert "No payment has been recorded" in payment_history.invoke(
            {"invoice_number": "INV-NEVER"})

    def test_a_paid_number_reports_the_amount_and_date(self, tmp_path, monkeypatch):
        import ledger
        db = tmp_path / "l.db"
        record("run-1", make_invoice(number="INV-1", total=500.0), db)
        monkeypatch.setattr(ledger, "DB_PATH", db)
        out = payment_history.invoke({"invoice_number": "INV-1"})
        assert "1 recorded payment" in out and "500.00" in out


class TestEverySystemFindingIsAnswerable:
    def test_each_one_has_a_lookup_that_bears_on_it(self):
        """A system-derived finding with no lookup behind it can never be objected to, which
        would be a silent dead end rather than a decision."""
        objectionable = {
            code for code, src in EVIDENCE_SOURCE.items()
            if src == "system" and not code.startswith("crit")
        }
        missing = objectionable - set(ANSWERED_BY)
        assert not missing, f"no lookup answers: {sorted(missing)}"

    def test_a_document_finding_needs_no_lookup(self):
        assert required_lookup("total_mismatch") is None


def objection(targets, quote):
    return Critique(verified=[], objections=[
        Objection(targets=targets, claim="c", problem="p", quote=quote)])


LEDGER_SAID = ("Invoice number INV-1 has 1 recorded payment(s): "
               "paid 500.00 USD to Widgets Inc. on 2026-10-09")


class TestAnObjectionAboutOurRecordsMustBeBackedByTheLookup:
    def _performed(self, tool="payment_history", result=LEDGER_SAID):
        return [{"tool": tool, "args": {"invoice_number": "INV-1"}, "result": result}]

    def test_quoting_what_the_lookup_returned_stands(self):
        sup, _ = ground(objection("duplicate_invoice_number", "paid 500.00 USD to Widgets"),
                        DOC, self._performed())
        assert len(sup) == 1

    def test_without_calling_it_the_objection_is_discarded(self):
        """This is the whole point. A tool that exists and was not called is no better than
        no tool: the critic is still guessing, just with an option it declined."""
        sup, unsup = ground(objection("duplicate_invoice_number", "paid 500.00 USD to Widgets"),
                            DOC, performed=[])
        assert sup == [] and len(unsup) == 1

    def test_calling_the_wrong_lookup_does_not_count(self):
        sup, _ = ground(objection("duplicate_invoice_number", "paid 500.00 USD to Widgets"),
                        DOC, self._performed(tool="catalogue"))
        assert sup == []

    def test_a_quote_the_lookup_did_not_return_is_discarded(self):
        sup, _ = ground(objection("duplicate_invoice_number", "no payment was ever recorded"),
                        DOC, self._performed())
        assert sup == [], "a fabricated lookup result is still a fabrication"

    def test_the_document_cannot_evidence_a_records_objection(self):
        """The original failure, in one assertion: quoting the invoice accurately proves
        nothing about what we have paid."""
        sup, unsup = ground(objection("duplicate_invoice_number", "INVOICE #INV-1"),
                            DOC, self._performed())
        assert sup == [] and len(unsup) == 1

    def test_and_a_lookup_cannot_evidence_a_document_objection(self):
        """Symmetry. Evidence has to come from the source that answers the question."""
        sup, _ = ground(objection("total_mismatch", "paid 500.00 USD to Widgets"),
                        DOC, self._performed())
        assert sup == []
