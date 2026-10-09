"""How a run is described to a human.

One module owns this wording so the dashboard and the CLI viewer can never describe the same
run two different ways - two tools disagreeing is what destroys trust in an audit trail.
"""

import re
from pathlib import Path

import pytest

from conftest import emitted_codes
from reasons import (ACTION_REQUIRED, LABELS, PRIORITY, REMEDIATION, SYSTEM_FLAGS,
                     category, headline, label, remediation)

SRC = Path(__file__).parent.parent / "src"
TAUTOLOGY = "not approved (decision was 'reject')"


def E(code, severity="error"):
    return {"code": code, "detail": f"detail for {code}", "severity": severity}


class TestEveryFindingHasWording:
    """A completeness guard. Adding a flag and forgetting its wording is the easy mistake,
    and the symptom is a dashboard row reading 'Quantity exceeds stock' in snake_case."""

    def test_every_emitted_code_has_a_label(self):
        missing = emitted_codes() - set(LABELS)
        assert not missing, f"no human label for: {sorted(missing)}"

    def test_every_emitted_code_has_remediation(self):
        missing = emitted_codes() - set(REMEDIATION)
        assert not missing, f"no remediation advice for: {sorted(missing)}"

    def test_every_invoice_finding_is_ranked(self):
        """System findings are deliberately unranked; invoice findings must be ordered."""
        missing = emitted_codes() - set(PRIORITY) - SYSTEM_FLAGS
        assert not missing, f"no priority for: {sorted(missing)}"

    def test_every_emitted_code_is_classified_by_evidence_source(self):
        """Unclassified, a finding defaults to system-derived and so cannot be objected to at
        all. That fails safe, but silently - and it also hid the missing-lookup guard below,
        which only ever looked at codes already in the map."""
        from validate import EVIDENCE_SOURCE
        missing = emitted_codes() - set(EVIDENCE_SOURCE)
        assert not missing, f"no evidence source recorded for: {sorted(missing)}"

    def test_an_unknown_code_degrades_readably(self):
        assert label("some_new_finding") == "Some new finding"


class TestCategory:
    @pytest.mark.parametrize("outcome,flags,expected", [
        ("paid", [], "paid"),
        ("rejected", [E("total_mismatch")], "denied"),
        ("rejected", [E("revises_paid_invoice")], "action"),
        ("rejected", [E("item_not_found")], "action"),
        ("escalated", [], "action"),
        (None, [], "action"),
        ("failed", [], "action"),
    ])
    def test_buckets(self, outcome, flags, expected):
        assert category(outcome, None, flags) == expected

    def test_a_processing_error_always_needs_a_person(self):
        assert category("rejected", "could not read the file", []) == "action"

    def test_action_required_findings_are_the_ones_implying_internal_work(self):
        """A vendor arithmetic error is denied and finished: send it back. These are
        different - money has already moved wrongly, or the denial may be ours to fix."""
        assert "revises_paid_invoice" in ACTION_REQUIRED
        assert "total_mismatch" not in ACTION_REQUIRED


class TestHeadline:
    def test_the_most_consequential_finding_leads(self):
        """Ordering is by consequence, not detection order. A duplicate payment is cash out
        of the door; a missing due date is paperwork."""
        out = headline("rejected", None, None,
                       [E("missing_due_date"), E("duplicate_invoice_number")], "reject")
        assert out.startswith("Duplicate invoice number")

    def test_other_findings_are_counted_not_hidden(self):
        out = headline("rejected", None, None,
                       [E("total_mismatch"), E("missing_due_date")], "reject")
        assert "+1 other finding)" in out and "findings)" not in out

    def test_pluralisation(self):
        out = headline("rejected", None, None,
                       [E("total_mismatch"), E("missing_due_date"), E("missing_vendor")],
                       "reject")
        assert "+2 other findings)" in out

    def test_the_reason_explains_the_bucket(self):
        """A row reading "math inconsistency" while filed under "needs a person" means the
        two halves of one row contradict each other."""
        flags = [E("total_mismatch"), E("item_not_found")]
        assert category("rejected", None, flags) == "action"
        assert headline("rejected", None, None, flags, "reject").startswith("Unknown item")

    def test_system_findings_are_never_the_reason_an_invoice_was_refused(self):
        """"The critic raised an objection it could not evidence" does not tell anyone why a
        vendor was refused."""
        out = headline("rejected", TAUTOLOGY, None,
                       [E("critique_unsupported", "warning")], "reject")
        assert "critic" not in out.lower()
        assert out == "Denied by approval review"

    def test_the_circular_gate_reason_is_not_used(self):
        """It read identically on all 21 rejected runs, and restates the outcome."""
        out = headline("rejected", TAUTOLOGY, None, [], "reject")
        assert "not approved" not in out

    def test_a_gate_override_is_called_out(self):
        """Code catching a judgment call. The first thing anyone auditing a bad payment
        needs to see."""
        out = headline("rejected", "INV-1 was already paid 500.00", None,
                       [E("duplicate_invoice_number")], "approve")
        assert "gate overrode the approval" in out

    def test_a_payment_the_critic_argued_for_says_so(self):
        """Calling INV-1010 "paid with a warning: the totals do not reconcile" would
        describe its first decision and misdescribe its outcome."""
        out = headline("paid", None, None, [E("total_mismatch")], "approve",
                       critique_rounds=1)
        assert out == "Paid after the critic forced 1 revision"

    def test_a_rejection_the_critic_failed_to_overturn_says_so(self):
        out = headline("rejected", TAUTOLOGY, None, [], "reject", critique_rounds=2)
        assert out == "Denied, upheld through 2 critic revisions"

    def test_clean_payment(self):
        assert headline("paid", None, None, [], "approve") == "Clean: no findings"

    def test_escalation_quotes_the_unresolved_objection(self):
        out = headline("escalated", None, None, [E("total_mismatch")], "reject",
                       "the critic still objects: the 50.00 gap is unexplained", 2)
        assert out.startswith("Unsettled after review")
        assert "50.00 gap" in out

    def test_an_unfinished_run_says_so(self):
        assert headline(None, None, None, []) == "Run did not finish - no outcome recorded"

    def test_a_processing_error_leads_with_the_cause(self):
        out = headline("failed", None, "scan.png is an image; OCR is not configured", [])
        assert out.startswith("Could not process the file")
        assert "OCR" in out


class TestRemediation:
    def test_advice_is_ordered_by_consequence(self):
        steps = remediation([E("missing_due_date"), E("revises_paid_invoice")])
        assert steps[0][0] == "Supersedes an invoice already paid"

    def test_each_finding_type_appears_once(self):
        steps = remediation([E("quantity_exceeds_stock"), E("quantity_exceeds_stock")])
        assert len(steps) == 1

    def test_no_findings_means_no_advice(self):
        assert remediation([]) == [] and remediation(None) == []

    def test_advice_is_specific_enough_to_act_on(self):
        _, advice = remediation([E("duplicate_invoice_number")])[0]
        assert "recover" in advice.lower()
