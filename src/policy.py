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

# How many times extraction may run on one document. Two means one cold attempt and one
# informed retry carrying the specific complaint about what did not check out.
#
# The reasoning for stopping at two is worth being able to give: the only thing that differs
# between attempts is the feedback, so a third carries the same complaint to the same model at
# the same temperature. "What would be different on attempt three?" has no good answer, and a
# person reading the document does.
MAX_EXTRACTION_ATTEMPTS = int(os.environ.get("MAX_EXTRACTION_ATTEMPTS", "2"))

# How many times the critic may send the approval agent back before the invoice goes to a
# person. Same reasoning: two informed revisions that have not settled it mean the system
# cannot settle it.
CRITIQUE_MAX_ROUNDS = int(os.environ.get("CRITIQUE_MAX_ROUNDS", "2"))

# How many rounds of lookups the critic may make before it has to write its critique. Three
# findings is already an unusual invoice, and a critic still gathering after this many calls is
# not converging on anything.
CRITIC_MAX_TOOL_STEPS = int(os.environ.get("CRITIC_MAX_TOOL_STEPS", "4"))
