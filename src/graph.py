"""The processing graph.

Nodes are added as they are built. Right now: ingest and extract.
"""

import time
import uuid
from langgraph.graph import END, START, StateGraph

import documents
from extract import extract
from state import InvoiceState
from validate import validate
from approve import approve, needs_scrutiny
from payment import gate, mock_payment
from runlog import finish_run, start_run, write_step


def ingest(state: InvoiceState) -> dict:
    """Read the document into text. No LLM: this is parsing, not judgment."""
    try:
        text, reader = documents.load(state["source_path"])
        return {"raw_text": text, "source_format": reader}
    except documents.DocumentError as exc:
        return {"processing_error": str(exc)}
    except Exception as exc:
        return {"processing_error": f"could not read {state['source_path']}: {exc}"}


def extract_node(state: InvoiceState) -> dict:
    """Pull structured fields out of the text. Agent: this is judgment."""
    if state.get("processing_error"):
        return {}
    try:
        invoice, prompt = extract(state["raw_text"])
        return {
            "invoice": invoice,
            "extraction_attempts": state.get("extraction_attempts", 0) + 1,
            "prompts": {**state.get("prompts", {}), "extract": prompt},
        }
    except Exception as exc:
        return {"processing_error": f"extraction failed: {exc}"}


def validate_node(state: InvoiceState) -> dict:
    """Check the extracted data. No LLM: lookups, arithmetic, and rules."""
    if state.get("processing_error") or not state.get("invoice"):
        return {}
    return {"flags": validate(state["invoice"])}


def route_node(state: InvoiceState) -> dict:
    """Code decides which approval lane. The agent never chooses its own."""
    if state.get("processing_error") or not state.get("invoice"):
        return {}
    return {"needs_scrutiny": needs_scrutiny(state["invoice"], state.get("flags", []))}


def approve_node(state: InvoiceState) -> dict:
    """Agent: judge whether this should be paid, given the findings."""
    if state.get("processing_error") or not state.get("invoice"):
        return {}
    try:
        d, prompt = approve(
            state["invoice"], state.get("flags", []), state.get("needs_scrutiny", False)
        )
        return {
            "approval_decision": d.decision,
            "approval_reasoning": d.reasoning,
            "prompts": {**state.get("prompts", {}), "approve": prompt},
        }
    except Exception as exc:
        return {"processing_error": f"approval failed: {exc}"}


def payment_node(state: InvoiceState) -> dict:
    """The gate, then the payment. Both code."""
    if state.get("processing_error") or not state.get("invoice"):
        return {}
    inv = state["invoice"]
    blocked = gate(inv, state.get("flags", []), state.get("approval_decision", "reject"))
    if blocked:
        return {"rejection_reason": blocked}
    return {"payment_result": mock_payment(state["run_id"], inv)}


def build_graph():
    g = StateGraph(InvoiceState)
    g.add_node("ingest", ingest)
    g.add_node("extract", extract_node)
    g.add_node("validate", validate_node)
    g.add_node("route", route_node)
    g.add_node("approve", approve_node)
    g.add_node("payment", payment_node)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "extract")
    g.add_edge("extract", "validate")
    g.add_edge("validate", "route")
    g.add_edge("route", "approve")
    g.add_edge("approve", "payment")
    g.add_edge("payment", END)
    return g.compile()


def process(source_path: str, log: bool = True) -> InvoiceState:
    """Run one invoice through the graph, recording what each node did.

    Logging wraps the graph rather than living inside each node: one place to forget
    instead of six. `stream` with updates gives each node's output as it lands.
    """
    initial = {"source_path": source_path, "run_id": str(uuid.uuid4())[:8], "flags": []}
    graph = build_graph()

    started = time.perf_counter()
    state: dict = dict(initial)
    if log:
        start_run(initial["run_id"], source_path)

    seq = 0
    for update in graph.stream(initial, stream_mode="updates"):
        for node, produced in update.items():
            if log:
                write_step(initial["run_id"], seq, node, produced or {})
            seq += 1
            state.update(produced or {})

    if log:
        finish_run(state, int((time.perf_counter() - started) * 1000))
    return state
