"""The processing graph.

Nodes are added as they are built. Right now: ingest and extract.
"""

import time
import uuid
from langgraph.graph import END, START, StateGraph

import citations
import documents
import inbox
import interventions
from citations import MAX_EXTRACTION_ATTEMPTS
from critique import MAX_ROUNDS, as_feedback, critique, ground
from extract import extract
from state import Flag, InvoiceState
from validate import validate
from approve import approve, needs_scrutiny
from payment import gate, mock_payment
from runlog import finish_run, start_run, was_processed, write_step


def ingest(state: InvoiceState) -> dict:
    """Read the document into text. No LLM: this is parsing, not judgment."""
    try:
        text, reader = documents.load(state["source_path"])
        return {"raw_text": text, "source_format": reader}
    except documents.DocumentError as exc:
        return {"processing_error": str(exc)}
    except Exception as exc:
        return {"processing_error": f"could not read {state['source_path']}: {exc}"}


def prescan_node(state: InvoiceState) -> dict:
    """Look at the rest of the inbox before spending anything on this invoice.

    Runs before extraction, and is pure regex and string comparison, so the cost of asking
    "is there another file here claiming to be this invoice" stays low enough to ask every
    time. The ledger already catches a superseded invoice, but only after the money has
    moved: invoice_1004 was paid at 1,890.00 while its revision sat in the same directory.

    Findings only. Which invoice gets processed is still decided by whoever runs the thing;
    this makes sure the decision is an informed one.
    """
    if state.get("processing_error"):
        return {}
    try:
        flags = inbox.precheck(
            state["source_path"],
            is_processed=was_processed,
            text=state.get("raw_text"),
        )
    except Exception as exc:
        # Reading the rest of the inbox is a convenience. If it fails, this invoice is still
        # perfectly processable and the ledger remains the backstop.
        return {"flags": [Flag(code="inbox_scan_failed", detail=str(exc), severity="warning")]}
    return {"flags": flags} if flags else {}


def extract_node(state: InvoiceState) -> dict:
    """Pull structured fields out of the text. Agent: this is judgment."""
    if state.get("processing_error"):
        return {}
    try:
        attempt = state.get("extraction_attempts", 0)
        invoice, prompt = extract(state["raw_text"], feedback=state.get("extraction_feedback"))
        out = {
            "invoice": invoice,
            "extraction_attempts": attempt + 1,
            "prompts": {**state.get("prompts", {}),
                        f"extract_{attempt}" if attempt else "extract": prompt},
            # Cleared once answered, so a later attempt cannot re-send a stale complaint.
            "extraction_feedback": None,
        }
        # A person's corrections go on here, not before: re-extracting the same document
        # reproduces the same misreading. Everything from validation onward then runs against
        # the corrected invoice, so the controls see what will actually be paid.
        if state.get("corrections"):
            invoice, applied = interventions.apply_corrections(
                invoice, state["corrections"], state.get("corrected_by") or "unknown")
            out["invoice"] = invoice
            out["corrections_applied"] = applied
        return out
    except Exception as exc:
        return {"processing_error": f"extraction failed: {exc}"}


def verify_citations_node(state: InvoiceState) -> dict:
    """Check every extracted figure against the text it claims to come from. Code, not an agent.

    This is what separates "we misread the document" from "the invoice is wrong", and those
    need opposite responses. A citation that is not in the document means another attempt is
    worth making. A citation that holds means the extractor read faithfully, so a figure that
    still does not reconcile is the vendor's problem and retrying would return the same answer
    forever.
    """
    if state.get("processing_error") or not state.get("invoice"):
        return {}

    problems = citations.verify(state["invoice"], state["raw_text"])
    if not problems:
        return {}

    attempts = state.get("extraction_attempts", 1)
    if attempts >= MAX_EXTRACTION_ATTEMPTS:
        # Out of attempts, and the values cannot be traced to the document. Escalating rather
        # than failing, because a person can read the document and say what it actually says -
        # which is exactly what the `misread` resolution is for.
        return {
            "citation_problems": problems,
            "escalation_reason": (
                f"extraction could not be verified against the document after {attempts} "
                f"attempts: {problems[0]}"
            ),
        }

    return {"citation_problems": problems,
            "extraction_feedback": citations.as_feedback(problems)}


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
        feedback = state.get("approval_feedback")
        # A person's recorded acceptance is shown to the agent as evidence, exactly like the
        # critic's objections are. It does not skip the agent and it does not skip the gate:
        # the human improves the input, the controls still run.
        d, prompt = approve(
            state["invoice"], state.get("flags", []), state.get("needs_scrutiny", False),
            feedback=feedback, waiver=state.get("waiver"),
        )
        round_no = state.get("critique_rounds", 0)
        key = f"approve_revision_{round_no}" if feedback else "approve"
        return {
            "approval_decision": d.decision,
            "approval_reasoning": d.reasoning,
            "approval_action": d.recommended_action,
            "prompts": {**state.get("prompts", {}), key: prompt},
            # Clear the complaint once it has been answered, so a later round cannot
            # silently re-send stale feedback.
            "approval_feedback": None,
        }
    except Exception as exc:
        return {"processing_error": f"approval failed: {exc}"}


def critic_node(state: InvoiceState) -> dict:
    """Agent: audit the approval reasoning against the source document.

    The critic sees the document; the approver never did. That asymmetry is the whole point -
    a critic given the same evidence and asked whether it agrees will agree.

    This node only ever reports. Whether an objection is strong enough to force a revision is
    decided by `ground()` in code, on the single deterministic question of whether the quote
    offered as evidence actually appears in the document.
    """
    if state.get("processing_error") or not state.get("invoice"):
        return {}
    try:
        crit, prompt, performed = critique(
            state["invoice"],
            state.get("flags", []),
            state.get("approval_decision", "reject"),
            state.get("approval_reasoning", ""),
            state["raw_text"],
        )
    except Exception as exc:
        # A critic that cannot run must not block payment on its own: the approval still
        # stands and the gate still applies. Record it and carry on.
        return {"flags": [Flag(code="critic_unavailable", detail=str(exc), severity="warning")]}

    supported, unsupported = ground(crit, state["raw_text"], performed)
    round_no = state.get("critique_rounds", 0)

    record = {
        "round": round_no,
        "reviewed_decision": state.get("approval_decision"),
        "reviewed_reasoning": state.get("approval_reasoning"),
        "verified": crit.verified,
        "lookups": performed,
        "grounded": [o.model_dump() for o in supported],
        "discarded": [o.model_dump() for o in unsupported],
    }
    out: dict = {
        "critiques": [record],
        "prompts": {**state.get("prompts", {}), f"critic_{round_no}": prompt},
    }

    # An objection whose quote is not in the document is an assertion, not evidence. Keep it
    # in the record - a critic that invents quotes is worth knowing about - but it carries no
    # authority to send the decision back.
    if unsupported:
        out["flags"] = [Flag(
            code="critique_unsupported",
            detail=(f"the critic raised {len(unsupported)} objection(s) whose quotes do not "
                    f"appear in the document; discarded"),
            severity="warning",
        )]

    if not supported:
        return out  # the reasoning held up. The decision stands.

    if round_no >= MAX_ROUNDS:
        # Informed revisions have not settled it. The system stops deciding and files the
        # case: neither paid nor rejected, because "we could not tell" is its own answer.
        out["escalation_reason"] = (
            f"the approval was revised {round_no} time(s) and the critic still objects: "
            f"{supported[0].problem}"
        )
        return out

    out["critique_rounds"] = round_no + 1
    out["approval_feedback"] = as_feedback(supported)
    return out


def payment_node(state: InvoiceState) -> dict:
    """The gate, then the payment. Both code."""
    if state.get("processing_error") or not state.get("invoice"):
        return {}
    inv = state["invoice"]
    blocked = gate(inv, state.get("flags", []), state.get("approval_decision", "reject"),
                   waived=state.get("waived_findings") or frozenset())
    if blocked:
        return {"rejection_reason": blocked}
    return {"payment_result": mock_payment(state["run_id"], inv)}


def still_running(state: InvoiceState) -> str:
    """Stop the line when the system has failed to do its job.

    Every node already guards itself, so without this a dead run still walked the whole graph
    returning {} from each remaining node. Nothing was spent and nothing moved, but the step
    log filled with five empty rows - noise in exactly the record you read when something has
    broken. A run that failed at ingest should show one step, not six.

    Note what this does NOT do: a validation finding is not a failure and never stops the
    line. A flag is something for the approval agent to weigh, and short-circuiting on one
    would mean no decision, no reasoning and no critic. invoice_1010 would still be denied,
    because total_mismatch would have ended the run before the critic could find the shipping
    line that explained it.

    The guard clauses stay regardless. They are what makes a node safe no matter which edge
    reached it, and an edge is easier to get wrong than a guard.
    """
    return "stop" if state.get("processing_error") else "continue"


def after_citations(state: InvoiceState) -> str:
    """Retry, give up, or carry on. All three decided in code."""
    if state.get("processing_error"):
        return "stop"
    if state.get("escalation_reason"):
        return "stop"
    return "retry" if state.get("extraction_feedback") else "continue"


def after_approval(state: InvoiceState) -> str:
    """Code decides whether this decision gets audited. The agent never opts out.

    Only the scrutiny path is critiqued. A clean invoice under the threshold has nothing for
    a critic to find, and running one anyway would buy latency and a transcript saying so.
    """
    if state.get("processing_error"):
        return "payment"
    return "critic" if state.get("needs_scrutiny") else "payment"


def after_critique(state: InvoiceState) -> str:
    """Revise, escalate, or proceed. All three are code decisions."""
    if state.get("processing_error"):
        return "payment"
    if state.get("escalation_reason"):
        return "escalate"
    return "approve" if state.get("approval_feedback") else "payment"


def build_graph():
    g = StateGraph(InvoiceState)
    g.add_node("ingest", ingest)
    g.add_node("prescan", prescan_node)
    g.add_node("extract", extract_node)
    g.add_node("verify_citations", verify_citations_node)
    g.add_node("validate", validate_node)
    g.add_node("route", route_node)
    g.add_node("approve", approve_node)
    g.add_node("critic", critic_node)
    g.add_node("payment", payment_node)
    g.add_edge(START, "ingest")
    # Ingest and extract are the two nodes that can fail outright: an unreadable file, or a
    # model that would not return the schema. Past those, a problem is a finding about the
    # invoice rather than a failure of ours, and findings must reach the approval agent.
    g.add_conditional_edges("ingest", still_running,
                            {"continue": "prescan", "stop": END})
    g.add_edge("prescan", "extract")
    g.add_conditional_edges("extract", still_running,
                            {"continue": "verify_citations", "stop": END})
    g.add_conditional_edges("verify_citations", after_citations,
                            {"retry": "extract", "continue": "validate", "stop": END})
    g.add_edge("validate", "route")
    g.add_edge("route", "approve")
    g.add_conditional_edges("approve", after_approval,
                            {"critic": "critic", "payment": "payment"})
    g.add_conditional_edges("critic", after_critique,
                            {"approve": "approve", "payment": "payment", "escalate": END})
    g.add_edge("payment", END)
    return g.compile()


def process(
    source_path: str,
    log: bool = True,
    intervention_id: str | None = None,
    supersedes_run: str | None = None,
    corrections: dict | None = None,
    corrected_by: str | None = None,
    waived: frozenset[str] = frozenset(),
    waiver: dict | None = None,
) -> InvoiceState:
    """Run one invoice through the graph, recording what each node did.

    Logging wraps the graph rather than living inside each node: one place to forget
    instead of six. `stream` with updates gives each node's output as it lands.
    """
    initial: dict = {"source_path": source_path, "run_id": str(uuid.uuid4())[:8], "flags": []}
    if intervention_id:
        # A resolved run is a new run. It is linked to the one it answers rather than
        # replacing it, so the escalation, the decision a person made, and the payment that
        # followed are all still there to read in order.
        initial.update({
            "intervention_id": intervention_id,
            "supersedes_run": supersedes_run,
            "corrections": corrections or {},
            "corrected_by": corrected_by,
            "waived_findings": waived,
            "waiver": waiver,
        })
    graph = build_graph()

    started = time.perf_counter()
    state: dict = dict(initial)
    if log:
        start_run(initial["run_id"], source_path)

    # Two stream modes, deliberately. "updates" gives what each node produced, which is what
    # the step log should record. "values" gives the accumulated state with the channel
    # reducers applied.
    #
    # Rebuilding state here with dict.update() instead was a real bug: `flags` declares an
    # append reducer, but a plain dict.update overwrites it. While `validate` was the only
    # node writing flags that was invisible; as soon as `critic` also wrote one, it erased
    # every validation finding from the run log. Reducers belong to the graph, not to a
    # hand-rolled copy of its state.
    seq = 0
    for mode, chunk in graph.stream(initial, stream_mode=["updates", "values"]):
        if mode == "updates":
            for node, produced in chunk.items():
                if log:
                    write_step(initial["run_id"], seq, node, produced or {})
                seq += 1
        elif mode == "values":
            state = chunk

    if log:
        finish_run(state, int((time.perf_counter() - started) * 1000))
    return state
