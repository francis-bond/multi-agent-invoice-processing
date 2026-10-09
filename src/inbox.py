"""Looking at the rest of the inbox before processing one invoice.

The ledger catches a superseded invoice after the money has moved. In the last full batch
invoice_1004 was paid at 1,890.00 and its revision was then correctly refused for superseding a
paid invoice - correct, and too late: the 1,890.00 now has to be recovered. The revision was
sitting in the same directory the whole time.

So this reads the other files first. It is all regex and string comparison, deliberately: the
question "is there another file here claiming to be this invoice" does not need judgment, and
spending a model call per sibling to answer it would make the check too expensive to run
before every invoice.

What it cannot do is decide which of two conflicting files is correct. It says they conflict
and hands that to a person, which is the honest outcome - a system that guesses between two
documents claiming to be the same invoice is worse than one that stops.
"""

import re
from pathlib import Path

import documents
from state import Flag

# "Invoice Number: INV-1011", "Inv #: 1002", "invoice": "INV-1013", <invoice_number>INV-1014
_NUMBER = re.compile(
    r"""(?ix)
    (?:invoice|inv)[\s_\-]*              # the label, however abbreviated
    (?:number|number>|no\.?|num|\#)?     # an optional qualifier
    \s*[:=>\#"']{0,3}\s*                 # whatever separates label from value
    "?((?:INV[\s\-_]?)?\d{3,})           # at least three digits: fewer is more likely a
                                         # quantity or a line number than an invoice number
    """
)

# "TOTAL:        $7,185.00", "total": -250.00, <total>4125.00</total>, "Amt: $15,000.00".
#
# Generous about what sits between the label and the figure, because plain-text invoices pad
# with spaces to line a column up - an allowance of six characters missed every one of them.
# The sign is captured: invoice_1009 states -250.00, and reading that as 250.00 would make a
# comparison against a sibling quietly wrong.
#
# \btotal deliberately does not match "SUBTOTAL", which is a different figure. "amt" and
# "amount due" are here because invoice_1002 never says "total" at all.
_TOTAL = re.compile(r"""(?ix)
    \b(?: total | amount\s+due | amt | balance\s+due )\b
    [^\d\n-]{0,24} (-?\s?[\d,]+\.\d{2})
""")

# Wording that marks a document as replacing an earlier one.
_REVISION = re.compile(r"(?i)\b(revis\w*|supersed\w*|amend\w*|replaces?|corrected)\b")


def number_of(text: str) -> str | None:
    """The invoice number, reduced to its digits.

    "INV-1011" and "1011" compare equal on purpose. Treating them as different numbers would
    miss a sibling, and a sibling wrongly matched produces a flag for a person rather than an
    automatic action - so the inclusive reading is the safe one.
    """
    m = _NUMBER.search(text)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    return digits or None


def total_of(text: str) -> float | None:
    """The stated total, if one can be read off without parsing the whole document.

    A heuristic on purpose. The authoritative total comes from extraction, which is an agent
    and costs a model call; this runs over every sibling before anything is processed, so
    paying for extraction here would make the check too expensive to run at all. When it
    cannot read a figure the comparison simply degrades to "these files share a number",
    and a misread produces a flag for a person rather than a payment.
    """
    # The last match, not the first: a grand total sits at the foot of an invoice, below any
    # column header or per-line amount that also says "total".
    found = _TOTAL.findall(text)
    if not found:
        return None
    try:
        return float(found[-1].replace(",", "").replace(" ", ""))
    except ValueError:
        return None


def revision_marker(text: str, filename: str) -> str | None:
    """What suggests this document replaces an earlier one, in its own words."""
    # Underscores are word characters, so \b never matches before "revised" in
    # "invoice_1004_revised" and filename detection silently never fired. Separators become
    # spaces first.
    stem = re.sub(r"[_\-.]+", " ", Path(filename).stem)
    in_name = _REVISION.search(stem)
    if in_name:
        return f"the filename says {in_name.group(1)!r}"
    for line in text.splitlines():
        found = _REVISION.search(line)
        if found:
            return line.strip()[:120]
    return None


def scan(directory: str | Path) -> dict[str, list[dict]]:
    """Read every file in a directory and group them by invoice number.

    Unreadable files are skipped rather than raised on. This runs before processing anything,
    and one corrupt file in the inbox must not stop the invoice someone is actually waiting on.
    """
    by_number: dict[str, list[dict]] = {}
    for path in sorted(Path(directory).iterdir()):
        if not path.is_file():
            continue
        try:
            text, _ = documents.load(path)
        except documents.DocumentError:
            continue
        number = number_of(text)
        if not number:
            continue
        by_number.setdefault(number, []).append({
            "path": str(path),
            "name": path.name,
            "number": number,
            "total": total_of(text),
            "revision": revision_marker(text, path.name),
        })
    return by_number


def precheck(path: str | Path, directory: str | Path | None = None,
             is_processed=None, text: str | None = None) -> list[Flag]:
    """Findings about the OTHER files claiming to be this invoice.

    `is_processed` decides whether a sibling has already been through the system; a sibling
    that was already paid is the ledger's problem and is left alone here. Injected rather than
    imported so this stays testable without a run log.
    """
    path = Path(path)
    directory = Path(directory) if directory else path.parent
    if text is None:
        try:
            text, _ = documents.load(path)
        except documents.DocumentError:
            return []
    number = number_of(text)
    if not number:
        return []

    mine = {"revision": revision_marker(text, path.name), "total": total_of(text)}
    siblings = [s for s in scan(directory).get(number, []) if Path(s["path"]) != path]
    if not siblings:
        return []

    flags: list[Flag] = []
    for s in siblings:
        if is_processed and is_processed(s["path"]):
            continue  # already been through; duplicate detection owns it from here

        theirs, ours = bool(s["revision"]), bool(mine["revision"])
        both_totals = mine["total"] is not None and s["total"] is not None
        differ = both_totals and abs(mine["total"] - s["total"]) > 0.01

        if theirs and not ours:
            # The one in hand is the older version. Paying it means recovering the money
            # later, which is the whole reason for looking before processing.
            flags.append(Flag(
                code="superseded_by_sibling",
                detail=(f"{s['name']} claims the same invoice number and marks itself a "
                        f"revision ({s['revision']}); this file does not. Process that one "
                        f"instead - paying this version means recovering it afterwards."),
                severity="error",
            ))
        elif ours and not theirs:
            # This is the version to process. Worth saying so, because the older file is
            # still sitting there for someone to pick up by mistake later.
            flags.append(Flag(
                code="supersedes_unprocessed_sibling",
                detail=(f"this file marks itself a revision ({mine['revision']}) and "
                        f"{s['name']} claims the same number without one. This is the version "
                        f"to process; withdraw the other so nobody picks it up later."),
                severity="warning",
            ))
        elif differ:
            # Same number, different money, and nothing says which replaces which. Guessing
            # between two documents claiming to be the same invoice is worse than stopping.
            both = "both mark themselves revisions" if ours else "neither marks itself a revision"
            flags.append(Flag(
                code="sibling_totals_differ",
                detail=(f"{s['name']} claims the same invoice number but states "
                        f"{s['total']:,.2f} against this file's {mine['total']:,.2f}, and "
                        f"{both}; a person has to say which is real"),
                severity="error",
            ))
        else:
            flags.append(Flag(
                code="sibling_file_same_number",
                detail=(f"{s['name']} claims the same invoice number"
                        + (" with the same total" if both_totals else "")
                        + "; likely the same invoice in another format, so expect one of "
                          "them to be refused as a duplicate"),
                severity="warning",
            ))
    return flags
