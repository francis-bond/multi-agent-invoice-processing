"""The processing graph.

Nodes are added as they are built. Right now: ingest and extract.
"""

import uuid
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from extract import extract
from state import InvoiceState


def ingest(state: InvoiceState) -> dict:
    """Read the document into text. No LLM: this is parsing, not judgment."""
    path = Path(state["source_path"])
    try:
        return {"raw_text": path.read_text()}
    except Exception as exc:
        return {"processing_error": f"could not read {path}: {exc}"}


def extract_node(state: InvoiceState) -> dict:
    """Pull structured fields out of the text. Agent: this is judgment."""
    if state.get("processing_error"):
        return {}
    try:
        invoice = extract(state["raw_text"])
        return {"invoice": invoice, "extraction_attempts": state.get("extraction_attempts", 0) + 1}
    except Exception as exc:
        return {"processing_error": f"extraction failed: {exc}"}


def build_graph():
    g = StateGraph(InvoiceState)
    g.add_node("ingest", ingest)
    g.add_node("extract", extract_node)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "extract")
    g.add_edge("extract", END)
    return g.compile()


def process(source_path: str) -> InvoiceState:
    return build_graph().invoke(
        {"source_path": source_path, "run_id": str(uuid.uuid4())[:8], "flags": []}
    )
