"""Checking extracted values against the text they claim to come from.

This is what separates "we misread the document" from "the invoice is wrong". Without it both
look like a number that does not reconcile, and the system either retries things that can
never improve or refuses invoices it simply failed to read.
"""

import json
from pathlib import Path

import pytest

import documents
from citations import MAX_EXTRACTION_ATTEMPTS, appears_in, as_feedback, normalise, verify
from conftest import ABSENT, cited, make_invoice
from models import ExtractedInvoice

FIXTURES = Path(__file__).parent / "fixtures" / "extracted"

DOC = """INVOICE #INV-1010
Vendor: Consolidated Materials Group
WidgetA                     8      $250.00     $2,000.00
                               Subtotal:     $6,700.00
                               Sales Tax:      $335.00
                               Shipping:       $150.00
                               TOTAL:        $7,185.00
"""


class TestAppearsIn:
    def test_a_verbatim_quote(self):
        assert appears_in("Shipping:       $150.00", DOC)

    def test_reflowed_whitespace_still_matches(self):
        """Models reflow padding when copying. Rejecting that would switch the check off
        rather than tighten it."""
        assert appears_in("Shipping: $150.00", DOC)

    def test_a_figure_that_is_not_there(self):
        assert not appears_in("Handling: $400.00", DOC)

    def test_a_wrong_figure(self):
        """The numbers are exactly the part that has to be right."""
        assert not appears_in("Subtotal: $6,700.99", DOC)

    def test_nothing_at_all(self):
        assert not appears_in(None, DOC) and not appears_in("", DOC)

    def test_a_fragment_too_short_to_identify_a_passage(self):
        assert not appears_in("$150", DOC)

    def test_one_definition_shared_with_the_critic(self):
        """Two copies of this would drift, and the two halves of the system would then
        disagree about what counts as evidence."""
        from critique import _normalise
        assert _normalise is normalise


class TestVerify:
    def _invoice(self, **kw):
        inv = make_invoice(items=[("WidgetA", 8, 250.0)], **kw)
        inv.line_items[0].source_text = "WidgetA                     8      $250.00"
        return inv

    def test_a_faithful_extraction_has_nothing_to_say(self):
        inv = self._invoice()
        inv.subtotal = cited(6700.0, "Subtotal:     $6,700.00")
        inv.tax_amount = cited(335.0, "Sales Tax:      $335.00")
        inv.total = cited(7185.0, "TOTAL:        $7,185.00")
        assert verify(inv, DOC) == []

    def test_a_figure_cited_to_text_that_is_not_there(self):
        inv = self._invoice()
        inv.total = cited(9999.0, "GRAND TOTAL: $9,999.00")
        problems = verify(inv, DOC)
        assert len(problems) == 1
        assert "total" in problems[0] and "does not appear" in problems[0]
        assert "report it as absent" in problems[0], "say what a correct answer looks like"

    def test_an_absent_figure_is_not_a_misreading(self):
        """A value the document never stated is a fact about the document, not an error."""
        inv = self._invoice()
        inv.subtotal, inv.tax_amount, inv.total = ABSENT, ABSENT, ABSENT
        assert verify(inv, DOC) == []

    def test_an_invented_line_item(self):
        inv = self._invoice()
        inv.line_items[0].source_text = "WidgetZ 99 @ $1.00"
        problems = verify(inv, DOC)
        assert len(problems) == 1 and "Line item 1" in problems[0]

    def test_an_invented_charge(self):
        from models import Charge
        inv = self._invoice()
        inv.charges = [Charge(label="Handling", amount=400.0,
                              source_text="Handling: $400.00")]
        problems = verify(inv, DOC)
        assert len(problems) == 1 and "Handling" in problems[0]

    def test_a_real_charge_verifies(self):
        from models import Charge
        inv = self._invoice()
        inv.charges = [Charge(label="Shipping", amount=150.0,
                              source_text="Shipping:       $150.00")]
        assert verify(inv, DOC) == []

    def test_every_problem_is_reported_not_just_the_first(self):
        inv = self._invoice()
        inv.total = cited(1.0, "nowhere near the document")
        inv.subtotal = cited(2.0, "also not in the document")
        inv.line_items[0].source_text = "nor is this line"
        assert len(verify(inv, DOC)) == 3


class TestTheRecordedExtractionsHoldUp:
    """Every citation in every fixture must be traceable to its source document.

    If the extractor is inventing citations, this is where it shows - and a fixture recorded
    from an invented citation would make the acceptance tests pass against a fiction.
    """

    @pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")),
                             ids=lambda p: p.stem)
    def test_citations_trace_back_to_the_document(self, path):
        data = json.loads(path.read_text())
        inv = ExtractedInvoice.model_validate(data["invoice"])
        text, _ = documents.load(Path("data/invoices") / data["source"])
        assert verify(inv, text) == []


class TestFeedback:
    def test_the_complaint_is_numbered_and_specific(self):
        text = as_feedback(["the total citation is not there", "line 2 is invented"])
        assert "1." in text and "2." in text
        assert "the total citation is not there" in text

    def test_the_attempt_limit_is_small_on_purpose(self):
        """One cold attempt and one informed retry. The only thing that differs between
        attempts is the feedback, so a third carries the same complaint to the same model at
        the same temperature - "what would be different?" has no good answer."""
        assert MAX_EXTRACTION_ATTEMPTS == 2
