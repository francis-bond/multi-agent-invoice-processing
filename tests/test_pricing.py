"""Price and counterparty checks: money we never agreed to.

This closes the largest hole the system had. Nothing else notices a unit price - arithmetic
only checks the figures agree with each other, the stock check only cares about quantity, and
the approval agent has no reference price to compare against.
"""

import pytest

from conftest import flag_codes, make_invoice
from inventory import resolve, vendor_is_approved
from policy import PRICE_TOLERANCE
from validate import _pricing, _vendor

pytestmark = pytest.mark.usefixtures("seeded_inventory")


class TestOvercharging:
    def test_a_ten_times_overcharge_is_caught(self):
        """The case that proved the hole. 2,500.00 for a 250.00 item is internally
        consistent, within stock, and from a known vendor: before this it would have paid."""
        flags = _pricing(make_invoice(items=[("WidgetA", 2, 2500.0)]))
        assert flag_codes(flags) == ["price_above_catalogue"]
        assert flags[0]["severity"] == "error"
        d = flags[0]["detail"]
        assert "2,500.00" in d and "250.00" in d
        assert "4,500.00 more than agreed" in d, "say what it costs, not just that it is high"

    def test_the_agreed_price_is_clean(self):
        assert _pricing(make_invoice(items=[("WidgetA", 2, 250.0)])) == []

    def test_a_discount_is_never_flagged(self):
        """A discount is the vendor's business. An invoice for less than the agreed price is
        not a risk to us, and flagging it would bury the overcharges in noise."""
        assert _pricing(make_invoice(items=[("WidgetA", 5, 240.0)])) == []   # 1013's discount
        assert _pricing(make_invoice(items=[("WidgetA", 5, 1.0)])) == []

    def test_a_premium_inside_the_tolerance_is_allowed(self):
        """A small premium for a rush or a short run is legitimate."""
        inside = 250.0 * (1 + PRICE_TOLERANCE) - 0.01
        assert _pricing(make_invoice(items=[("WidgetA", 1, inside)])) == []

    def test_a_premium_outside_the_tolerance_is_flagged(self):
        outside = 250.0 * (1 + PRICE_TOLERANCE) + 0.01
        assert flag_codes(_pricing(make_invoice(items=[("WidgetA", 1, outside)]))) \
            == ["price_above_catalogue"]

    def test_the_rush_order_premium_on_invoice_1010_is_surfaced(self):
        """300.00 against an agreed 250.00 is 20% over. Possibly legitimate, and a person
        should be the one to say so."""
        flags = _pricing(make_invoice(items=[("WidgetA (rush order)", 4, 300.0)]))
        assert flag_codes(flags) == ["price_above_catalogue"]
        assert "20%" in flags[0]["detail"]

    def test_a_spelling_variant_is_still_priced(self):
        """Price checking has to go through the same matching as the stock check, or an
        overcharge hides behind a space."""
        assert flag_codes(_pricing(make_invoice(items=[("Widget A", 1, 2500.0)]))) \
            == ["price_above_catalogue"]

    def test_an_unknown_item_is_not_double_reported(self):
        """There is no agreed price for something we do not stock, and item_not_found has
        already said so."""
        assert _pricing(make_invoice(items=[("WidgetC", 1, 99999.0)])) == []

    def test_the_catalogue_carries_a_price(self):
        m = resolve("WidgetA")
        assert m.unit_price == 250.0


class TestTheApprovedSupplierList:
    @pytest.mark.parametrize("name", ["Widgets Inc.", "Atlas Industrial Supply",
                                      "Summit Manufacturing Co."])
    def test_approved_vendors_pass(self, name):
        assert vendor_is_approved(name)
        assert _vendor(make_invoice(vendor=name)) == []

    @pytest.mark.parametrize("name", ["Fraudster LLC", "NoProd Industries", "Nobody At All"])
    def test_a_vendor_not_on_the_list_is_flagged(self, name):
        assert not vendor_is_approved(name)
        flags = _vendor(make_invoice(vendor=name))
        assert flag_codes(flags) == ["unknown_vendor"]
        assert flags[0]["severity"] == "error"

    @pytest.mark.parametrize("variant", ["Widgets Inc", "widgets inc.", "WIDGETS  INC."])
    def test_punctuation_and_case_do_not_make_a_new_supplier(self, variant):
        """Matched the same way item names are, or "Widgets Inc." and "Widgets Inc" would be
        one approved supplier and one unknown."""
        assert vendor_is_approved(variant)

    def test_a_missing_vendor_is_not_reported_twice(self):
        """missing_vendor already covers an absent payee; saying it again as unknown_vendor
        would make one problem look like two."""
        assert _vendor(make_invoice(vendor=None)) == []
        assert _vendor(make_invoice(vendor="   ")) == []
