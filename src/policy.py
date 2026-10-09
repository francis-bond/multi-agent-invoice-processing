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
