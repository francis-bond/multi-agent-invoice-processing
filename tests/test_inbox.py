"""Reading the rest of the inbox before processing one invoice.

All regex and string comparison, no model. The question "is there another file here claiming
to be this invoice" does not need judgment, and paying for a model call per sibling would make
the check too expensive to run before every invoice - which would mean not running it.
"""

import pytest

from inbox import number_of, precheck, revision_marker, scan, total_of
from conftest import flag_codes


class TestReadingAnInvoiceNumber:
    @pytest.mark.parametrize("text,expected", [
        ("Invoice Number: INV-1011", "1011"),
        ("Inv #: 1002", "1002"),            # no prefix at all
        ('"invoice_number": "INV-1009"', "1009"),
        ("<invoice_number>INV-1014</invoice_number>", "1014"),
        ("Invoice: INV-1013 Date: 2026-01-24", "1013"),
        ("INV NO: INV 1012", "1012"),       # a space where a hyphen usually is
    ])
    def test_the_formats_that_actually_appear(self, text, expected):
        assert number_of(text) == expected

    def test_two_digits_is_not_an_invoice_number(self):
        """More likely a quantity or a line number, and a false match would group unrelated
        files together."""
        assert number_of("Inv #: 42") is None

    def test_a_prefix_does_not_make_a_different_invoice(self):
        """Treating "INV-1011" and "1011" as different numbers would miss a sibling. A
        sibling wrongly matched only produces a flag for a person, so the inclusive reading
        is the safe one."""
        assert number_of("Invoice: INV-1011") == number_of("Inv #: 1011")

    def test_no_number_at_all(self):
        assert number_of("a letter about nothing in particular") is None


class TestReadingATotal:
    @pytest.mark.parametrize("text,expected", [
        ("TOTAL:        $7,185.00", 7185.0),     # padded to line a column up
        ('"total": 22562.80', 22562.80),
        ("<total>4125.00</total>", 4125.0),
        ("Total:,15525.00", 15525.0),            # csv
        ("Amt: $15,000.00", 15000.0),            # invoice_1002 never says "total"
    ])
    def test_the_formats_that_actually_appear(self, text, expected):
        assert total_of(text) == expected

    def test_a_negative_total_keeps_its_sign(self):
        """invoice_1009 states -250.00. Reading that as 250.00 would make a comparison
        against a sibling quietly wrong."""
        assert total_of('"total": -250.00') == -250.0

    def test_a_subtotal_is_a_different_figure(self):
        assert total_of("Subtotal: $3,000.00\nTotal: $3,300.00") == 3300.0

    def test_the_last_match_wins(self):
        """A grand total sits at the foot of an invoice, under any column header or per-line
        amount that also says "total"."""
        assert total_of("ITEM QTY TOTAL\nWidgetA 2 500.00\nTotal: 500.00") == 500.0


class TestSpottingARevision:
    @pytest.mark.parametrize("text", [
        '"revision": "R1"',
        "Revised invoice - additional items added per PO amendment",
        "This supersedes our earlier invoice",
        "CORRECTED COPY",
    ])
    def test_wording_in_the_document(self, text):
        assert revision_marker(text, "invoice.txt")

    def test_wording_in_the_filename(self):
        assert "filename" in revision_marker("nothing here", "invoice_1004_revised.json")

    def test_an_ordinary_invoice_has_none(self):
        assert revision_marker("Invoice Number: INV-1\nTotal: $5.00", "invoice_1001.txt") is None


class TestScanningTheRealInbox:
    def test_every_sample_file_yields_a_number(self):
        found = sum(len(v) for v in scan("data/invoices").values())
        assert found == 20, "a file whose number cannot be read is invisible to this check"

    def test_the_sibling_groups_are_the_expected_four(self):
        groups = {k: sorted(f["name"] for f in v)
                  for k, v in scan("data/invoices").items() if len(v) > 1}
        assert set(groups) == {"1004", "1011", "1012", "1013"}
        assert groups["1004"] == ["invoice_1004.json", "invoice_1004_revised.json"]


class TestPrecheck:
    """Nothing is processed yet, which is when this check matters."""

    def test_the_superseded_file_is_held(self):
        """The payment this exists to prevent. invoice_1004 was paid at 1,890.00 while its
        revision sat in the same directory, and the 1,890.00 then had to be recovered."""
        flags = precheck("data/invoices/invoice_1004.json")
        assert flag_codes(flags) == ["superseded_by_sibling"]
        assert flags[0]["severity"] == "error", "must be able to stop the payment"
        assert "invoice_1004_revised.json" in flags[0]["detail"]

    def test_the_revision_is_the_one_to_process(self):
        """And it must not be told that neither file claims to be a revision, which is what
        it used to say: the branch only handled the sibling being the newer one."""
        flags = precheck("data/invoices/invoice_1004_revised.json")
        assert flag_codes(flags) == ["supersedes_unprocessed_sibling"]
        assert flags[0]["severity"] == "warning", "this is the right file; do not block it"
        assert "withdraw the other" in flags[0]["detail"]

    def test_two_formats_of_one_invoice_raise_nothing_on_the_first_copy(self):
        """Flagging it made the approval agent refuse whichever copy was processed first, so
        which format got paid came down to directory order - and "another file claims this
        number" is not a reason to refuse anything. The second copy is caught by the ledger,
        where there is an actual prior payment to point at."""
        assert precheck("data/invoices/invoice_1011.pdf") == []
        assert precheck("data/invoices/invoice_1013.json") == []

    def test_an_unreadable_total_on_a_twin_is_still_worth_saying(self, tmp_path):
        """Whether they agree is unknown, and the duplicate check cannot tell a second format
        from a second bill."""
        (tmp_path / "a.txt").write_text("Invoice Number: INV-7701\nTotal: $100.00")
        (tmp_path / "b.txt").write_text("Invoice Number: INV-7701\nno figure here at all")
        flags = precheck(tmp_path / "a.txt", tmp_path)
        assert flag_codes(flags) == ["sibling_file_same_number"]
        assert flags[0]["severity"] == "warning"

    def test_an_invoice_with_no_siblings_is_clean(self):
        assert precheck("data/invoices/invoice_1001.txt") == []

    def test_a_sibling_already_dealt_with_is_left_alone(self):
        """Once the other file has been through the system, duplicate detection owns it and
        saying so again here would be noise."""
        assert precheck("data/invoices/invoice_1004.json",
                        is_processed=lambda p: True) == []

    def test_conflicting_totals_with_no_revision_marker_stop_for_a_person(self, tmp_path):
        """A system that guesses between two documents claiming to be the same invoice is
        worse than one that stops."""
        (tmp_path / "a.txt").write_text("Invoice Number: INV-7701\nTotal: $100.00")
        (tmp_path / "b.txt").write_text("Invoice Number: INV-7701\nTotal: $900.00")
        flags = precheck(tmp_path / "a.txt", tmp_path)
        assert flag_codes(flags) == ["sibling_totals_differ"]
        assert flags[0]["severity"] == "error"
        assert "neither marks itself a revision" in flags[0]["detail"]

    def test_an_unreadable_file_in_the_inbox_does_not_stop_the_scan(self, tmp_path):
        """This runs before processing anything, so one corrupt file must not block the
        invoice someone is waiting on."""
        (tmp_path / "good.txt").write_text("Invoice Number: INV-88\nTotal: $100.00")
        (tmp_path / "junk.rtf").write_text("not readable")
        (tmp_path / "binary.txt").write_bytes(bytes(range(200, 256)) * 4)
        assert precheck(tmp_path / "good.txt", tmp_path) == []
