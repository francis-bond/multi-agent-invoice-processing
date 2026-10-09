"""Configurable control values, in one file.

These are the numbers that decide how an invoice is handled, so they are deliberately not
scattered through the code that uses them. An auditor asking "what are the rules?" should be
able to read them without reading the implementation, and changing one should not mean a code
change.
"""

import os

# What we are able to pay. An invoice in anything else cannot be priced without an exchange
# rate, and this system has no rate source. Converting would mean inventing a number.
HOME_CURRENCY = os.environ.get("HOME_CURRENCY", "USD").upper()

# Above this, an invoice takes the scrutiny path regardless of how clean it looks. Denominated
# in HOME_CURRENCY, which is why comparing a foreign total against it is meaningless.
SCRUTINY_THRESHOLD = float(os.environ.get("SCRUTINY_THRESHOLD", "10000"))

# How far above the catalogue price a line may be billed before it is flagged. A vendor may
# legitimately charge a premium for a rush or a short run, so a small margin is allowed; what
# this exists to catch is a unit price that bears no relation to what was agreed.
#
# Underpricing is never flagged. A discount is the vendor's business, and an invoice for less
# than the agreed price is not a risk to us.
PRICE_TOLERANCE = float(os.environ.get("PRICE_TOLERANCE", "0.10"))
