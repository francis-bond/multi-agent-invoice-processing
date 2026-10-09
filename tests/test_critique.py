"""Quote grounding: the control decision inside the critic loop.

The critic supplies judgment and evidence. Whether the evidence EXISTS is a fact, and facts
are not a model's to assert - so code checks it. An objection that cannot be grounded carries
no authority to send a decision back.
"""

import pytest

from critique import Critique, Objection, as_feedback, ground

DOC = """INVOICE #INV-1010
Vendor: Consolidated Materials Group

WidgetA                     8      $250.00     $2,000.00
                               Subtotal:     $6,700.00
                               Sales Tax:      $335.00
                               Shipping:       $150.00
                               TOTAL:        $7,185.00
"""

CSV_DOC = (
    "Invoice Number,Vendor,Item,Qty\n"
    ",,,,,,Subtotal:,14750.00\n"
    ",,,,,,Tax (6%):,885.00\n"
    ",,,,,,Total:,15525.00\n"
)


def obj(quote, claim="a claim", problem="a problem", targets="total_mismatch"):
    """Defaults to a document-derived target, which is the in-scope case."""
    return Objection(targets=targets, claim=claim, problem=problem, quote=quote)


def crit(*quotes, verified=()):
    return Critique(verified=list(verified), objections=[obj(q) for q in quotes])


def crit_targeting(finding, quote):
    return Critique(verified=[], objections=[obj(quote, targets=finding)])


class TestGrounded:
    def test_a_verbatim_quote_is_supported(self):
        supported, unsupported = ground(crit("Shipping:       $150.00"), DOC)
        assert len(supported) == 1 and unsupported == []

    def test_reflowed_whitespace_still_grounds(self):
        """Models reflow text when copying. Rejecting that would disable the check, not
        tighten it."""
        supported, _ = ground(crit("Shipping: $150.00"), DOC)
        assert len(supported) == 1

    def test_case_is_ignored(self):
        supported, _ = ground(crit("shipping: $150.00"), DOC)
        assert len(supported) == 1

    def test_a_quote_spanning_csv_rows_grounds(self):
        """The regression. A critic quoting three CSV rows joined them with commas where the
        document has newlines. Strict matching discarded a verifiable quote and silently
        switched the critic off across every CSV invoice - a fifth of the corpus."""
        quote = "Subtotal:,14750.00,,,,,,Tax (6%):,885.00,,,,,,Total:,15525.00"
        supported, unsupported = ground(crit(quote), CSV_DOC)
        assert len(supported) == 1, "CSV reformatting must not discard a real quote"
        assert unsupported == []


class TestNotGrounded:
    def test_a_fabricated_quote_is_discarded(self):
        supported, unsupported = ground(crit("Handling Fee: $400.00"), DOC)
        assert supported == [] and len(unsupported) == 1

    def test_a_wrong_figure_is_discarded(self):
        """The loosened matching must not let 14750.00 pass as 14750.99. The numbers are
        exactly the part that has to be right."""
        supported, _ = ground(crit("Subtotal:,14750.99"), CSV_DOC)
        assert supported == []

    def test_an_empty_quote_is_discarded(self):
        """Observed in a real run. The model satisfied the schema with an empty string -
        the same shape as the currency: "" bug, and again only code caught it."""
        supported, unsupported = ground(crit(""), DOC)
        assert supported == [] and len(unsupported) == 1

    @pytest.mark.parametrize("fragment", ["$1", "TOTAL", "   ", "a"])
    def test_a_fragment_too_short_to_identify_a_passage_is_discarded(self, fragment):
        supported, _ = ground(crit(fragment), DOC)
        assert supported == []

    def test_grounded_and_ungrounded_are_separated_not_merged(self):
        c = crit("Shipping:       $150.00", "Handling Fee: $400.00")
        supported, unsupported = ground(c, DOC)
        assert len(supported) == 1 and len(unsupported) == 1
        # Ungrounded objections stay in the record: a critic that invents quotes is worth
        # knowing about. They just carry no authority.
        assert unsupported[0].quote == "Handling Fee: $400.00"


class TestScope:
    """The critic reads the document. It cannot overturn what the document cannot speak to.

    This is the control that stops the failure that nearly paid twice: the critic argued a
    duplicate payment was impossible because no duplicate notice appeared in the document,
    quoting the document accurately, and the approval agent believed it.
    """

    @pytest.mark.parametrize("finding", ["total_mismatch", "subtotal_mismatch",
                                         "negative_quantity", "missing_due_date",
                                         "foreign_currency", "item_on_multiple_lines"])
    def test_a_document_derived_finding_can_be_challenged(self, finding):
        supported, _ = ground(crit_targeting(finding, "Shipping:       $150.00"), DOC)
        assert len(supported) == 1, f"{finding} is readable from the document"

    @pytest.mark.parametrize("finding", ["duplicate_invoice_number", "revises_paid_invoice",
                                         "possible_duplicate_billing", "item_not_found",
                                         "quantity_exceeds_stock", "item_out_of_stock",
                                         "price_above_catalogue", "unknown_vendor"])
    def test_a_system_derived_finding_cannot_be(self, finding):
        """Our payment history, catalogue and supplier list are not in the invoice. An
        invoice we already paid does not announce it, and a vendor we never approved does
        not say so, so the document's silence proves nothing."""
        supported, unsupported = ground(
            crit_targeting(finding, "Shipping:       $150.00"), DOC)
        assert supported == [], f"{finding} is not the critic's to overturn"
        assert len(unsupported) == 1, "but it stays in the record"

    def test_the_exact_failure_that_nearly_paid_twice(self):
        """Verbatim from a real run: a correctly quoted, coherent, wrong objection."""
        c = Critique(verified=[], objections=[Objection(
            targets="duplicate_invoice_number",
            claim="the invoice was already paid",
            problem=("The document date 2026-01-15 precedes the alleged prior payment date "
                     "2026-10-09, and no duplicate notice appears in the document"),
            quote="INVOICE #INV-1010")])
        supported, unsupported = ground(c, DOC)
        assert supported == [], "a January invoice paid in October is not a contradiction"
        assert len(unsupported) == 1

    def test_an_objection_to_the_reasoning_itself_is_in_scope(self):
        supported, _ = ground(crit_targeting("reasoning", "Shipping:       $150.00"), DOC)
        assert len(supported) == 1

    def test_an_unclassified_finding_fails_safe(self):
        """A new finding nobody has classified cannot be argued away until someone decides
        it should be."""
        supported, _ = ground(crit_targeting("some_new_check", "Shipping:       $150.00"), DOC)
        assert supported == []

    def test_scope_and_grounding_are_independent(self):
        """In scope but unquoted is still discarded."""
        supported, _ = ground(crit_targeting("total_mismatch", "Handling: $400.00"), DOC)
        assert supported == []


class TestFeedback:
    def test_the_revision_carries_the_specific_complaint(self):
        """An uninformed retry asks the same question and gets the same answer."""
        o = obj("Shipping:       $150.00",
                claim="the figures do not reconcile",
                problem="a shipping charge explains the exact difference of 150")
        text = as_feedback([o])
        assert "the figures do not reconcile" in text
        assert "shipping charge explains" in text
        assert "Shipping:       $150.00" in text

    def test_several_objections_are_numbered(self):
        text = as_feedback([obj("a" * 20), obj("b" * 20)])
        assert "1." in text and "2." in text
