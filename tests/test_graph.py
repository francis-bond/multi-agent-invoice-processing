"""The graph: control flow around the agents, with no agent actually called.

What is asserted is never the model's judgment - that is not a testable property. It is
everything the surrounding code does with the judgment: which lane an invoice takes, whether
a revision is requested, when the loop stops, and what survives into the final state.
"""

import pytest

from approve import ApprovalDecision
from conftest import flag_codes, make_invoice
from critique import Critique, Objection

DOC = "INVOICE INV-TEST\nWidgetA x2 @ 250.00\nSubtotal: 500.00\nShipping: 25.00\nTOTAL: 525.00\n"


@pytest.fixture
def invoice_file(tmp_path):
    p = tmp_path / "invoice_test.txt"
    p.write_text(DOC)
    return str(p)


@pytest.fixture
def stub(monkeypatch):
    """Replace every model call and the payment side effect.

    Returns a recorder so a test can assert how many times the approver ran, and set what the
    approver and critic return on each successive call.
    """
    import graph

    class Stub:
        def __init__(self):
            self.invoice = make_invoice(items=[("WidgetA", 2, 250.0)],
                                        subtotal=500.0, total=525.0,
                                        charges=[("Shipping", 25.0)])
            self.decisions = [("approve", "it reconciles")]
            self.critiques = [Critique(verified=["total checks out"], objections=[])]
            self.approve_calls = []
            self.critic_calls = 0
            self.paid = []

        def _decision(self, n):
            d = self.decisions[min(n, len(self.decisions) - 1)]
            return ApprovalDecision(decision=d[0], reasoning=d[1],
                                    recommended_action="do the thing")

        def _critique(self, n):
            return self.critiques[min(n, len(self.critiques) - 1)]

    s = Stub()

    monkeypatch.setattr(graph, "extract", lambda text, model=None: (s.invoice, "extract prompt"))
    monkeypatch.setattr(graph, "approve",
                        lambda inv, flags, scrutiny, model=None, feedback=None: (
                            s.approve_calls.append(feedback) or
                            s._decision(len(s.approve_calls) - 1), "approve prompt"))

    def fake_critique(inv, flags, decision, reasoning, document, model=None):
        c = s._critique(s.critic_calls)
        s.critic_calls += 1
        return c, "critic prompt"

    monkeypatch.setattr(graph, "critique", fake_critique)
    monkeypatch.setattr(graph, "mock_payment",
                        lambda run_id, inv: (s.paid.append(inv.total.value)
                                             or {"status": "success", "amount": inv.total.value}))
    # Validation and the gate must not see this machine's payment history.
    import payment, validate
    monkeypatch.setattr(validate, "prior_by_number", lambda inv: [])
    monkeypatch.setattr(validate, "prior_by_content", lambda inv: [])
    monkeypatch.setattr(payment, "prior_by_number", lambda inv: [])
    return s


def run(path):
    from graph import process
    return process(path, log=False)


class TestFlagsSurviveToTheEnd:
    def test_validation_findings_are_not_erased_by_a_later_node(self, invoice_file, stub,
                                                               seeded_inventory):
        """The reducer regression, and the most valuable test here.

        `flags` declares an append reducer, but process() rebuilt its own state with
        dict.update(), which overwrites. While validate was the only node writing flags this
        was invisible. The moment the critic also wrote one it erased every validation
        finding: a rejected invoice logged a single critic warning and no trace of the stock
        errors or the mismatch that actually caused the rejection.
        """
        # An invoice with a real finding, and a critic that also writes one.
        stub.invoice = make_invoice(items=[("WidgetA", 20, 250.0)], subtotal=5000.0,
                                    total=5000.0)
        stub.decisions = [("reject", "over stock")]
        stub.critiques = [Critique(verified=[], objections=[
            Objection(claim="c", problem="p", quote="not in the document at all")])]

        state = run(invoice_file)
        codes = flag_codes(state["flags"])
        assert "quantity_exceeds_stock" in codes, "validation findings must survive"
        assert "critique_unsupported" in codes, "the critic's own finding must survive too"


class TestRouting:
    def test_a_clean_invoice_under_threshold_skips_the_critic(self, invoice_file, stub,
                                                              seeded_inventory):
        """Running a critic on an invoice with nothing to find buys latency and a transcript
        saying so."""
        state = run(invoice_file)
        assert state["needs_scrutiny"] is False
        assert stub.critic_calls == 0
        assert stub.paid == [525.0]

    def test_any_flag_sends_an_invoice_to_the_critic(self, invoice_file, stub,
                                                     seeded_inventory):
        stub.invoice = make_invoice(items=[("WidgetA", 2, 250.0), ("Widget A", 1, 250.0)],
                                    subtotal=750.0, total=750.0)
        state = run(invoice_file)
        assert state["needs_scrutiny"] is True
        assert stub.critic_calls == 1


class TestTheCriticLoop:
    def _scrutiny_invoice(self):
        # A warning is enough to route into the loop without creating an error.
        return make_invoice(items=[("WidgetA", 2, 250.0), ("Widget A", 1, 250.0)],
                            subtotal=750.0, total=750.0)

    def test_an_ungrounded_objection_does_not_force_a_revision(self, invoice_file, stub,
                                                               seeded_inventory):
        stub.invoice = self._scrutiny_invoice()
        stub.critiques = [Critique(verified=[], objections=[
            Objection(claim="c", problem="p", quote="a quote that is not in the document")])]
        state = run(invoice_file)
        assert len(stub.approve_calls) == 1, "the decision should stand"
        assert state.get("critique_rounds", 0) == 0
        assert "critique_unsupported" in flag_codes(state["flags"])

    def test_a_grounded_objection_forces_an_informed_revision(self, invoice_file, stub,
                                                              seeded_inventory):
        stub.invoice = self._scrutiny_invoice()
        stub.decisions = [("reject", "does not reconcile"), ("approve", "shipping explains it")]
        stub.critiques = [
            Critique(verified=[], objections=[Objection(
                claim="does not reconcile", problem="shipping explains the gap",
                quote="Shipping: 25.00")]),
            Critique(verified=["shipping accounted for"], objections=[]),
        ]
        state = run(invoice_file)
        assert len(stub.approve_calls) == 2, "the approver should be asked again"
        assert stub.approve_calls[0] is None, "the first pass carries no feedback"
        assert "shipping explains the gap" in stub.approve_calls[1], \
            "the revision must carry the specific complaint"
        assert state["approval_decision"] == "approve"
        assert state["critique_rounds"] == 1

    def test_a_persistent_objection_escalates_rather_than_rejecting(self, invoice_file, stub,
                                                                    seeded_inventory):
        """Two informed revisions that have not settled it mean the system cannot decide.
        "We could not tell" is its own answer and must not be filed as a rejection."""
        stub.invoice = self._scrutiny_invoice()
        stub.decisions = [("reject", "no")]
        stub.critiques = [Critique(verified=[], objections=[Objection(
            claim="c", problem="the gap is unexplained", quote="Shipping: 25.00")])]
        state = run(invoice_file)
        assert state.get("escalation_reason"), "should escalate"
        assert "the gap is unexplained" in state["escalation_reason"]
        assert stub.paid == [], "an escalated invoice is not paid"
        assert state.get("payment_result") is None

    def test_the_loop_cannot_run_forever(self, invoice_file, stub, seeded_inventory):
        from critique import MAX_ROUNDS
        stub.invoice = self._scrutiny_invoice()
        stub.decisions = [("reject", "no")]
        stub.critiques = [Critique(verified=[], objections=[Objection(
            claim="c", problem="p", quote="Shipping: 25.00")])]
        run(invoice_file)
        assert len(stub.approve_calls) == MAX_ROUNDS + 1

    def test_a_critic_that_cannot_run_does_not_veto_the_payment(self, invoice_file, stub,
                                                                seeded_inventory,
                                                                monkeypatch):
        """A broken auditor must not become a veto. The approval stands, the gate still
        applies, and a flag records that the audit did not happen."""
        import graph

        def boom(*a, **k):
            raise RuntimeError("model unreachable")

        monkeypatch.setattr(graph, "critique", boom)
        stub.invoice = self._scrutiny_invoice()
        state = run(invoice_file)
        assert "critic_unavailable" in flag_codes(state["flags"])
        assert state.get("processing_error") is None, "a critic outage is not a processing failure"
        assert stub.paid == [750.0]


class TestFailuresStopTheLine:
    def test_an_unreadable_file_does_not_reach_the_agents(self, tmp_path, stub):
        bad = tmp_path / "invoice.rtf"
        bad.write_text("x")
        state = run(str(bad))
        assert "no reader for" in state["processing_error"]
        assert stub.approve_calls == [] and stub.critic_calls == 0
        assert stub.paid == []

    def test_an_extraction_failure_is_a_processing_error_not_a_rejection(self, invoice_file,
                                                                        stub, monkeypatch):
        """The invoice may be perfectly fine; we could not process it. A person should look
        at the document, which is a different outcome from the invoice being bad."""
        import graph

        def boom(text, model=None):
            raise RuntimeError("structured output refused")

        monkeypatch.setattr(graph, "extract", boom)
        state = run(invoice_file)
        assert "extraction failed" in state["processing_error"]
        assert state.get("approval_decision") is None
        assert stub.paid == []
