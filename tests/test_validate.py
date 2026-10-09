"""Validation: every check deterministic, every problem reported rather than raised."""

import pytest

from conftest import ABSENT, cited, flag_codes, make_invoice
from validate import _arithmetic, _existence_and_stock, _sanity, validate

pytestmark = pytest.mark.usefixtures("seeded_inventory")


class TestStockIsAggregatedAcrossLines:
    def test_split_order_is_caught(self):
        """invoice_1013's shape. Each line passes alone; together they exceed stock.

        Checking lines individually misses every violation on an invoice that splits its
        order, which is both a normal billing pattern and the obvious way to slip an
        oversized order past a naive check.
        """
        inv = make_invoice(items=[("WidgetA", 10, 250.0), ("WidgetA", 7, 250.0)])
        flags = _existence_and_stock(inv)
        assert "quantity_exceeds_stock" in flag_codes(flags)
        detail = next(f for f in flags if f["code"] == "quantity_exceeds_stock")["detail"]
        assert "17" in detail and "15" in detail and "2 lines" in detail

    def test_split_across_SPELLINGS_is_caught(self):
        """The regression this suite exists for.

        Grouping on the vendor's spelling made "WidgetA 12" and "Widget A 5" two products of
        12 and 5, and both passed a stock of 15. Varying the spacing between lines defeated
        the aggregate check entirely.
        """
        inv = make_invoice(items=[("WidgetA", 12, 250.0), ("Widget A", 5, 250.0)])
        flags = _existence_and_stock(inv)
        assert "quantity_exceeds_stock" in flag_codes(flags)
        assert "17" in next(f for f in flags
                            if f["code"] == "quantity_exceeds_stock")["detail"]

    def test_within_stock_is_clean(self):
        inv = make_invoice(items=[("WidgetA", 10, 250.0), ("Widget A", 5, 250.0)])
        assert flag_codes(_existence_and_stock(inv)) == ["item_on_multiple_lines"]

    def test_flags_quote_the_invoices_own_wording(self):
        """The audit trail shows what the vendor wrote, not what we matched it to."""
        inv = make_invoice(items=[("WidgetA", 1, 250.0), ("Widget A", 1, 250.0)])
        detail = next(f for f in _existence_and_stock(inv)
                      if f["code"] == "item_on_multiple_lines")["detail"]
        assert "'WidgetA'" in detail and "'Widget A'" in detail


class TestExistence:
    def test_unknown_item(self):
        inv = make_invoice(items=[("WidgetC", 1, 10.0)])
        assert flag_codes(_existence_and_stock(inv)) == ["item_not_found"]

    def test_out_of_stock_is_a_different_finding_from_unknown(self):
        inv = make_invoice(items=[("FakeItem", 1, 10.0)])
        assert flag_codes(_existence_and_stock(inv)) == ["item_out_of_stock"]

    def test_two_spellings_of_one_unknown_item_report_once(self):
        inv = make_invoice(items=[("Widget C", 1, 10.0), ("widgetc", 1, 10.0)])
        assert flag_codes(_existence_and_stock(inv)).count("item_not_found") == 1

    def test_loose_match_warns_per_line_not_per_group(self):
        """invoice_1010 lists both "WidgetA" and "WidgetA (rush order)".

        Keeping only the first line's match result silently dropped the second line's
        qualifier. Looseness is a property of a line.
        """
        inv = make_invoice(items=[("WidgetA", 2, 250.0), ("WidgetA (rush order)", 1, 300.0)])
        flags = _existence_and_stock(inv)
        loose = [f for f in flags if f["code"] == "item_matched_loosely"]
        assert len(loose) == 1
        assert "(rush order)" in loose[0]["detail"]
        assert loose[0]["severity"] == "warning"   # matched, not refused


class TestArithmetic:
    def test_clean_invoice_reconciles(self):
        inv = make_invoice(items=[("WidgetA", 2, 250.0)], subtotal=500.0, tax=25.0, total=525.0)
        assert _arithmetic(inv) == []

    def test_charges_are_part_of_the_expected_total(self):
        """invoice_1010. Shipping is real money and explains the gap."""
        inv = make_invoice(items=[("WidgetA", 2, 250.0)], subtotal=500.0, tax=25.0,
                           total=575.0, charges=[("Shipping", 50.0)])
        assert _arithmetic(inv) == []

    def test_negative_charge_is_a_discount_not_a_special_case(self):
        inv = make_invoice(items=[("WidgetA", 2, 250.0)], subtotal=500.0, tax=25.0,
                           total=475.0, charges=[("Discount", -50.0)])
        assert _arithmetic(inv) == []

    def test_genuine_vendor_error_is_still_caught_when_charges_exist(self):
        """The charges fix must not become a licence to mis-add."""
        inv = make_invoice(items=[("WidgetA", 2, 250.0)], subtotal=500.0, tax=25.0,
                           total=600.0, charges=[("Shipping", 50.0)])
        flags = _arithmetic(inv)
        assert flag_codes(flags) == ["total_mismatch"]
        assert "Shipping" in flags[0]["detail"]   # names what it accounted for

    def test_unexplained_gap_is_reported(self):
        """invoice_1013: no charge line anywhere, 50.00 over. A real vendor error."""
        inv = make_invoice(items=[("WidgetA", 2, 250.0)], subtotal=500.0, tax=25.0, total=575.0)
        assert flag_codes(_arithmetic(inv)) == ["total_mismatch"]

    def test_subtotal_mismatch(self):
        inv = make_invoice(items=[("WidgetA", 2, 250.0)], subtotal=400.0, total=400.0)
        assert "subtotal_mismatch" in flag_codes(_arithmetic(inv))

    def test_an_uncited_figure_is_not_reconciled_against(self):
        """A figure the document never stated is not a mismatch; claiming one invents a problem.

        invoice_1003 and invoice_1008 state no subtotal. Before `Cited`, a defaulted 0.00 was
        indistinguishable from a stated zero and both reported a false mismatch.
        """
        inv = make_invoice(items=[("WidgetA", 2, 250.0)], total=525.0)
        inv.subtotal, inv.tax_amount = ABSENT, cited(25.0)
        assert _arithmetic(inv) == []   # falls back to the computed line total

    def test_a_stated_zero_is_reconciled_against(self):
        """invoice_1001 genuinely states "Tax (0%): $0.00". Zero stated is a fact."""
        inv = make_invoice(items=[("WidgetA", 2, 250.0)], subtotal=500.0, total=500.0)
        inv.tax_amount = cited(0.0)
        assert _arithmetic(inv) == []
        assert inv.tax_amount.was_stated


class TestSanity:
    @pytest.mark.parametrize("items,code", [
        ([("WidgetA", -2, 250.0)], "negative_quantity"),
        ([("WidgetA", 0, 250.0)], "zero_quantity"),
        ([("WidgetA", 1, -250.0)], "negative_unit_price"),
    ])
    def test_impossible_line_values(self, items, code):
        assert code in flag_codes(_sanity(make_invoice(items=items)))

    def test_negative_total(self):
        inv = make_invoice(total=-250.0)
        assert "negative_total" in flag_codes(_sanity(inv))

    @pytest.mark.parametrize("kwargs,code", [
        ({"vendor": None}, "missing_vendor"),
        ({"number": None}, "missing_invoice_number"),
        ({"due_date": None}, "missing_due_date"),
    ])
    def test_missing_required_fields(self, kwargs, code):
        assert code in flag_codes(_sanity(make_invoice(**kwargs)))

    def test_no_line_items(self):
        assert "no_line_items" in flag_codes(_sanity(make_invoice(items=[])))


class TestValidateReportsEverything:
    def test_an_invoice_with_five_problems_reports_five(self, no_ledger):
        """Checks return flags rather than raising, so the log shows the whole picture."""
        inv = make_invoice(items=[("WidgetC", -1, -5.0)], vendor=None, due_date=None,
                           subtotal=100.0, total=999.0)
        codes = set(flag_codes(validate(inv)))
        assert {"item_not_found", "negative_quantity", "negative_unit_price",
                "missing_vendor", "missing_due_date", "subtotal_mismatch"} <= codes
