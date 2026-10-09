"""Catalogue matching. Staged, declared, and all code - none of this is fuzzy matching."""

import pytest

from inventory import canonical, resolve, strip_annotation

pytestmark = pytest.mark.usefixtures("seeded_inventory")


class TestCanonical:
    @pytest.mark.parametrize("written", ["WidgetA", "Widget A", "widget a", "WIDGET-A",
                                         "widget_a", "Widget.A", "  WidgetA  "])
    def test_separators_and_case_carry_no_product_identity(self, written):
        assert canonical(written) == canonical("WidgetA")

    def test_different_products_stay_different(self):
        assert canonical("WidgetA") != canonical("WidgetB")
        # The digit is identity, not punctuation. Folding must not collapse these.
        assert canonical("Widget1") != canonical("Widget2")


class TestStripAnnotation:
    @pytest.mark.parametrize("written,bare", [
        ("WidgetA (rush order)", "WidgetA"),
        ("WidgetA [backorder]", "WidgetA"),
        ("GadgetX (expedited)", "GadgetX"),
        ("WidgetA", "WidgetA"),              # nothing to strip
        ("(not) WidgetA", "(not) WidgetA"),  # only a TRAILING qualifier
    ])
    def test_only_trailing_qualifiers_are_dropped(self, written, bare):
        assert strip_annotation(written) == bare


class TestResolve:
    def test_exact_match_is_reported_as_exact(self):
        m = resolve("WidgetA")
        assert (m.item, m.stock, m.how) == ("WidgetA", 15, "exact")
        assert m.found and not m.is_loose

    def test_spelling_variant_resolves_to_the_catalogue_name(self):
        m = resolve("Widget A")
        assert (m.item, m.stock, m.how) == ("WidgetA", 15, "normalized")
        # Lossless, so it must NOT be reported as a loose match.
        assert not m.is_loose

    def test_annotated_name_matches_but_is_flagged_loose(self):
        """Stripping a qualifier discards information that may carry a price change."""
        m = resolve("WidgetA (rush order)")
        assert (m.item, m.how) == ("WidgetA", "annotation")
        assert m.is_loose

    def test_unknown_item_is_not_rescued_by_normalising(self):
        m = resolve("WidgetC")
        assert not m.found
        assert (m.stock, m.item, m.how) == (None, None, "unmatched")

    def test_stocked_but_empty_is_not_the_same_as_absent(self):
        """None means we do not stock it; 0 means we stock it and have none."""
        empty, absent = resolve("FakeItem"), resolve("NoSuchThing")
        assert empty.found and empty.stock == 0
        assert not absent.found and absent.stock is None
