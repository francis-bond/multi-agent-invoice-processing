"""The payment gate.

Not a third validation. An assert immediately before the irreversible action: it re-examines
nothing and re-decides nothing, it refuses to let certain conditions reach a bank transfer
whatever anyone upstream concluded. Seatbelt, not checkpoint.

Everything that decides before this point is a language model. This is the only deterministic
thing between a model's opinion and real money leaving an account.
"""

import pytest

from conftest import make_invoice
from payment import gate


@pytest.fixture
def no_prior(monkeypatch):
    import payment
    monkeypatch.setattr(payment, "prior_by_number", lambda inv: [])


class TestItLetsGoodPaymentsThrough:
    def test_approved_and_reconciling_invoice_passes(self, no_prior):
        inv = make_invoice(items=[("WidgetA", 2, 250.0)], subtotal=500.0, total=525.0)
        assert gate(inv, [], "approve") is None


class TestItRefusesRegardlessOfTheApproval:
    """Each of these is approved by the agent. The gate says no anyway."""

    def test_an_unapproved_decision_cannot_pay(self, no_prior):
        inv = make_invoice(total=500.0)
        assert "not approved" in gate(inv, [], "reject")

    def test_an_invoice_with_no_stated_total_has_nothing_to_pay(self, no_prior):
        inv = make_invoice()
        assert "no total" in gate(inv, [], "approve")

    @pytest.mark.parametrize("total", [0.0, -250.0])
    def test_non_positive_totals(self, no_prior, total):
        inv = make_invoice(total=total)
        assert "positive" in gate(inv, [], "approve")

    def test_a_negative_quantity_blocks_even_when_approved(self, no_prior):
        inv = make_invoice(items=[("WidgetA", -2, 250.0)], subtotal=-500.0, total=500.0)
        assert "negative quantity" in gate(inv, [], "approve")

    def test_an_unreconciled_invoice_cannot_pay(self, no_prior):
        """Guards a value drifting between the decision and the transfer."""
        inv = make_invoice(items=[("WidgetA", 2, 250.0)], subtotal=900.0, total=900.0)
        assert "unreconciled" in gate(inv, [], "approve")

    def test_it_consults_the_ledger_rather_than_trusting_upstream(self, monkeypatch):
        """The duplicate-payment regression.

        INV-1004 was paid twice during testing: once as the original and again as the
        revision. Validation flags a duplicate, but the gate must not depend on that having
        happened - it looks for itself, immediately before the money moves.
        """
        import payment
        monkeypatch.setattr(payment, "prior_by_number",
                            lambda inv: [{"amount": 500.0, "paid_at": "2026-10-09T00:00:00"}])
        inv = make_invoice(items=[("WidgetA", 2, 250.0)], subtotal=500.0, total=525.0)
        blocked = gate(inv, [], "approve")
        assert "already paid" in blocked and "twice" in blocked


class TestItDoesNotSecondGuessJudgment:
    def test_warnings_do_not_block_an_approved_payment(self, no_prior):
        """The gate enforces hard rules. Whether a warning is material is the agent's call,
        and re-litigating it here would make the gate a second approver."""
        inv = make_invoice(items=[("WidgetA", 2, 250.0)], subtotal=500.0, total=525.0)
        flags = [{"code": "item_matched_loosely", "detail": "x", "severity": "warning"},
                 {"code": "item_on_multiple_lines", "detail": "x", "severity": "warning"}]
        assert gate(inv, flags, "approve") is None
