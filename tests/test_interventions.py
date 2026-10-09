"""Human intervention: the record, and the limits on what a person may decide.

An intervention supplies a better input. It never supplies the verdict, so the re-run goes
through every control. These tests are mostly about what the system refuses, because the
refusals are the controls.
"""

import json

import pytest

import interventions
from conftest import make_invoice
from interventions import (InterventionError, NEVER_WAIVABLE, apply_corrections, get,
                           history, link_run, record, waived_by)
from payment import gate

GOOD = "confirmed with the supplier by phone and checked against the purchase order"


@pytest.fixture
def db(tmp_path):
    return tmp_path / "ledger.db"


def an_intervention(db, **kw):
    kw.setdefault("run_id", "run-1")
    kw.setdefault("invoice_number", "INV-1")
    kw.setdefault("resolution", "accept")
    kw.setdefault("actor", "F Bond")
    kw.setdefault("justification", GOOD)
    return record(db_path=db, **kw)


class TestWhatItRefusesToRecord:
    def test_an_unattributed_override_is_not_an_audit_record(self, db):
        for actor in ("", "   ", None):
            with pytest.raises(InterventionError, match="actor"):
                an_intervention(db, actor=actor)

    def test_a_justification_is_required_in_their_own_words(self, db):
        """The next person reading this will have only that."""
        for weak in ("", "ok", "fine", "approved"):
            with pytest.raises(InterventionError, match="justification"):
                an_intervention(db, justification=weak)

    def test_an_unknown_resolution(self, db):
        with pytest.raises(InterventionError, match="not a resolution"):
            an_intervention(db, resolution="just_pay_it")

    @pytest.mark.parametrize("code", sorted(NEVER_WAIVABLE))
    def test_some_findings_are_never_a_materiality_judgement(self, db, code):
        """Paying against a record of a prior payment is money leaving twice for one debt.
        If a further payment is owed it is owed against a new document."""
        with pytest.raises(InterventionError, match="cannot be waived"):
            an_intervention(db, waived=[code])

    def test_only_an_acceptance_waives_anything(self, db):
        """Correcting a misreading is not a reason to stop checking something else."""
        with pytest.raises(InterventionError, match="only waived by"):
            an_intervention(db, resolution="records_wrong", waived=["item_not_found"])

    def test_a_misreading_has_to_say_what_was_read_wrongly(self, db):
        with pytest.raises(InterventionError, match="what we read wrongly"):
            an_intervention(db, resolution="misread", corrections=None)


class TestTheRecord:
    def test_everything_an_auditor_needs_is_kept(self, db):
        iid = an_intervention(db, waived=["item_not_found"],
                              saw_findings=["item_not_found", "item_on_multiple_lines"])
        row = get(iid, db)
        assert row["actor"] == "F Bond"
        assert row["resolution"] == "accept"
        assert row["justification"] == GOOD
        assert row["run_id"] == "run-1"
        assert json.loads(row["waived"]) == ["item_not_found"]
        assert json.loads(row["saw_findings"]) == ["item_not_found", "item_on_multiple_lines"]
        assert row["at"].endswith("+00:00"), "stored in UTC"

    def test_the_chain_from_payment_back_to_escalation_is_traceable(self, db):
        """"Why was this paid?" has to lead back through the decision to the escalation."""
        iid = an_intervention(db)
        assert get(iid, db)["resulting_run"] is None
        link_run(iid, "run-2", db)
        assert get(iid, db)["resulting_run"] == "run-2"

    def test_history_is_per_invoice_and_ordered(self, db):
        an_intervention(db, run_id="run-1")
        an_intervention(db, run_id="run-2")
        an_intervention(db, invoice_number="INV-OTHER")
        rows = history("INV-1", db)
        assert [r["run_id"] for r in rows] == ["run-1", "run-2"]
        assert history(None, db) == []


class TestWaiversAreScoped:
    def test_a_waiver_covers_what_that_person_saw(self, db):
        iid = an_intervention(db, waived=["item_not_found"])
        assert waived_by(iid, db) == frozenset({"item_not_found"})

    def test_an_unrelated_intervention_grants_nothing(self, db):
        """A problem appearing for the first time on a re-run was never seen by anyone and
        does not inherit someone else's approval."""
        an_intervention(db, waived=["item_not_found"])
        assert waived_by(None, db) == frozenset()
        assert waived_by("nonexistent", db) == frozenset()

    def test_a_never_waivable_finding_cannot_arrive_through_a_stored_record(self, db):
        """Belt and braces: even if a row were written directly, reading it back filters."""
        iid = an_intervention(db)
        import sqlite3
        conn = sqlite3.connect(db)
        conn.execute("UPDATE interventions SET waived=? WHERE intervention_id=?",
                     (json.dumps(["item_not_found", "duplicate_invoice_number"]), iid))
        conn.commit(); conn.close()
        assert waived_by(iid, db) == frozenset({"item_not_found"})


class TestTheGateHonoursAWaiverAndNothingWider:
    @pytest.fixture(autouse=True)
    def no_prior(self, monkeypatch):
        import payment
        monkeypatch.setattr(payment, "prior_by_number", lambda inv: [])

    def _inv(self):
        return make_invoice(items=[("WidgetA", 2, 250.0)], subtotal=500.0, total=500.0)

    def test_a_waived_finding_no_longer_blocks(self):
        flags = [{"code": "item_not_found", "detail": "d", "severity": "error"}]
        assert gate(self._inv(), flags, "approve") is not None
        assert gate(self._inv(), flags, "approve",
                    waived=frozenset({"item_not_found"})) is None

    def test_an_unwaived_finding_still_blocks(self):
        """A second problem does not ride along on a waiver of the first."""
        flags = [{"code": "item_not_found", "detail": "d", "severity": "error"},
                 {"code": "unknown_vendor", "detail": "d", "severity": "error"}]
        blocked = gate(self._inv(), flags, "approve", waived=frozenset({"item_not_found"}))
        assert blocked is not None

    def test_an_absolute_is_not_waivable_at_the_gate_either(self):
        inv = make_invoice(items=[("WidgetA", 2, 250.0)], subtotal=500.0, total=-500.0)
        assert gate(inv, [], "approve", waived=frozenset({"negative_total"})) is not None


class TestCorrections:
    def test_a_text_field(self):
        inv, applied = apply_corrections(make_invoice(vendor="Wrong Co."),
                                         {"vendor": "Widgets Inc."}, "F Bond")
        assert inv.vendor == "Widgets Inc."
        assert "vendor:" in applied[0]

    def test_a_figure_and_its_citation(self):
        """A corrected figure is vouched for by a person, not transcribed. The record says
        which, and it still counts as stated because it IS on the invoice."""
        inv, _ = apply_corrections(make_invoice(total=7035.0), {"total": "7185.00"}, "F Bond")
        assert inv.total.value == 7185.0
        assert inv.total.was_stated
        assert "F Bond" in inv.total.source_text

    def test_a_non_numeric_figure_is_refused(self):
        with pytest.raises(InterventionError, match="has to be a number"):
            apply_corrections(make_invoice(total=1.0), {"total": "about seven grand"}, "x")

    def test_a_line_item_is_not_correctable_here(self):
        """A line read wrongly enough to matter is a re-extraction; a line wrong on the
        invoice is the vendor's to reissue. Neither is a field edit."""
        with pytest.raises(InterventionError, match="not correctable"):
            apply_corrections(make_invoice(), {"line_items": "[]"}, "x")

    def test_an_unknown_field_is_refused_and_says_what_is_allowed(self):
        with pytest.raises(InterventionError, match="Correctable fields are"):
            apply_corrections(make_invoice(), {"grand_total": "5"}, "x")

    def test_clearing_a_field(self):
        inv, _ = apply_corrections(make_invoice(revision="R1"), {"revision": ""}, "x")
        assert inv.revision is None
