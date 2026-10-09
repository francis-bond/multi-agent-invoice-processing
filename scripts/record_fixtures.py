"""Re-record the extraction fixtures the acceptance tests replay.

Run this when the extraction schema changes. It costs one model call per sample and is the
only part of the test suite that needs an API key:

    uv run python scripts/record_fixtures.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

import documents  # noqa: E402
from extract import extract  # noqa: E402

SAMPLES = [
    "invoice_1002.txt", "invoice_1003.txt", "invoice_1008.txt", "invoice_1009.json",
    "invoice_1016.json", "invoice_1014.xml", "invoice_1010.txt", "invoice_1013.json",
    "invoice_1007.csv",
]
OUT = ROOT / "tests" / "fixtures" / "extracted"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name in SAMPLES:
        text, reader = documents.load(ROOT / "data" / "invoices" / name)
        inv, _ = extract(text)
        path = OUT / f"{name.rsplit('.', 1)[0]}.json"
        path.write_text(json.dumps(
            {"source": name, "reader": reader, "invoice": inv.model_dump()}, indent=2) + "\n")
        print(f"  {name:<22} -> {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
