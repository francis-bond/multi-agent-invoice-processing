"""Acceptance tests against the real sample invoices.

The brief names five scenarios its sample set is built to exercise. This file asserts the
system still detects each of them, so the answer to "do we match what they specified?" comes
from CI rather than from someone querying a database by hand.

Extractions are recorded in tests/fixtures/extracted/ and replayed, so these run offline and
free. That means they test validation, routing and the gate - not the extractor, which needs
a model and is covered by running the thing. Re-record with scripts/record_fixtures.py when
the extraction schema changes.
"""

import json
from pathlib import Path

import pytest

from approve import needs_scrutiny
from conftest import flag_codes
from models import ExtractedInvoice
from payment import gate
from validate import validate

FIXTURES = Path(__file__).parent / "fixtures" / "extracted"

pytestmark = pytest.mark.usefixtures("seeded_inventory", "no_ledger")


def load(stem):
    data = json.loads((FIXTURES / f"{stem}.json").read_text())
    return ExtractedInvoice.model_validate(data["invoice"])


@pytest.fixture(autouse=True)
def no_prior_payments(monkeypatch):
    """Judge each invoice on its own merits, not on this machine's payment history."""
    import payment
    monkeypatch.setattr(payment, "prior_by_number", lambda inv: [])


# (fixture, what the brief says this invoice is for, the finding that proves we caught it)
BRIEF_SCENARIOS = [
    ("invoice_1002", "quantity exceeds available stock", "quantity_exceeds_stock"),
    ("invoice_1003", "FakeItem: stocked but zero on hand", "item_out_of_stock"),
    ("invoice_1008", "items that do not exist in the catalogue", "item_not_found"),
    ("invoice_1009", "negative quantity", "negative_quantity"),
    ("invoice_1016", "WidgetC, an unknown item", "item_not_found"),
]


class TestTheBriefsScenarios:
    @pytest.mark.parametrize("stem,description,expected", BRIEF_SCENARIOS,
                             ids=[s[0] for s in BRIEF_SCENARIOS])
    def test_the_specified_condition_is_detected(self, stem, description, expected):
        codes = flag_codes(validate(load(stem)))
        assert expected in codes, f"{stem} ({description}) should raise {expected}, got {codes}"

    @pytest.mark.parametrize("stem,description,expected", BRIEF_SCENARIOS,
                             ids=[s[0] for s in BRIEF_SCENARIOS])
    def test_a_rejected_decision_never_pays(self, stem, description, expected):
        """Detection is not the point; not paying is."""
        inv = load(stem)
        assert gate(inv, validate(inv), "reject") is not None

    def test_an_absolute_violation_is_blocked_whatever_the_agent_decided(self):
        """invoice_1009 has a negative total and a negative quantity. Those are the gate's
        own rules, so an approval cannot get past them."""
        inv = load("invoice_1009")
        assert gate(inv, validate(inv), "approve") is not None

    @pytest.mark.xfail(reason="open design question: should the gate be the backstop for "
                              "'these goods may not exist', or is materiality a judgment "
                              "call the agent owns? See README known gaps.",
                       strict=True)
    @pytest.mark.parametrize("stem", ["invoice_1002", "invoice_1003", "invoice_1008",
                                      "invoice_1016"])
    def test_stock_findings_are_not_currently_gate_blocking(self, stem):
        """Documents a real hole rather than hiding it.

        The gate refuses on absolutes only - no total, non-positive total, negative quantity,
        already paid, unreconciled subtotal. An unknown or unstocked item is none of those, so
        if the approval agent ever approved one of these, the money would move. In practice
        the agent rejects all four, which means the only thing between an LLM mistake and a
        payment for goods we have no record of stocking is the LLM.

        Marked xfail(strict) deliberately: if the gate is extended to cover these, this test
        fails loudly and gets deleted, rather than quietly passing and being forgotten.
        """
        inv = load(stem)
        assert gate(inv, validate(inv), "approve") is not None

    @pytest.mark.parametrize("stem,_d,_e", BRIEF_SCENARIOS, ids=[s[0] for s in BRIEF_SCENARIOS])
    def test_each_takes_the_scrutiny_path(self, stem, _d, _e):
        inv = load(stem)
        assert needs_scrutiny(inv, validate(inv)) is True


class TestCasesWeAdded:
    """Beyond the five. The brief invites expanding the assumptions and adding cases."""

    def test_eur_invoice_is_refused_rather_than_paid_as_a_bare_number(self):
        """invoice_1014.xml. We extracted EUR correctly and then paid 4125.0 with no unit on
        it. There is no exchange rate source, so a person has to price this."""
        inv = load("invoice_1014")
        assert inv.currency == "EUR"
        assert "foreign_currency" in flag_codes(validate(inv))
        assert "EUR" in gate(inv, [], "approve")

    def test_an_unmodelled_shipping_charge_does_not_look_like_bad_arithmetic(self):
        """invoice_1010. 6,700 + 335 + 150 shipping = 7,185, which is the stated total. The
        schema had no field for shipping, so a correct invoice reported a false mismatch."""
        inv = load("invoice_1010")
        assert [(c.label, c.amount) for c in inv.charges] == [("Shipping", 150.0)]
        assert "total_mismatch" not in flag_codes(validate(inv))

    def test_that_fix_does_not_hide_a_real_arithmetic_error(self):
        """invoice_1013. Line items sum to exactly the stated subtotal, tax is exactly 7%,
        and the total is 50.00 over with no charge line anywhere in the document."""
        inv = load("invoice_1013")
        assert inv.charges == []
        assert "total_mismatch" in flag_codes(validate(inv))

    def test_the_awkward_csv_is_read_without_collapsing_its_line_items(self):
        """invoice_1007.csv is field,value pairs with `item` repeated, so naive parsing
        collapses the lines into one."""
        inv = load("invoice_1007")
        assert len(inv.line_items) >= 2
        codes = flag_codes(validate(inv))
        assert "quantity_exceeds_stock" in codes
        assert "total_mismatch" in codes   # a genuine 110.00 vendor error

    def test_an_invoice_number_is_transcribed_not_corrected(self):
        """invoice_1002.txt writes "1002", not "INV-1002". The extractor transcribes what the
        document says; normalising it in the prompt would destroy the audit trail."""
        assert load("invoice_1002").invoice_number == "1002"

    def test_every_problem_is_reported_not_just_the_first(self):
        """invoice_1009 is the brief's negative-quantity case and has five more problems.
        Checks return flags rather than raising, so the log shows the whole picture."""
        codes = set(flag_codes(validate(load("invoice_1009"))))
        assert {"negative_quantity", "negative_total", "missing_vendor",
                "missing_due_date"} <= codes
        assert len(codes) >= 5
