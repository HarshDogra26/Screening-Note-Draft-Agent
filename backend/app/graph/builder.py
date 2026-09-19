from __future__ import annotations
import uuid
from typing import Any
from langgraph.graph import END, START, StateGraph
from ..logging import get_logger
from . import nodes
from .state import NoteState, initial_state

log = get_logger(__name__)


def build():
    graph = StateGraph(NoteState)

    graph.add_node("route", nodes.route)
    graph.add_node("describe", nodes.describe)
    graph.add_node("plan", nodes.plan)
    graph.add_node("gather", nodes.gather)
    graph.add_node("detect", nodes.detect)
    graph.add_node("draft", nodes.draft)
    graph.add_node("assemble", nodes.assemble)
    graph.add_node("refuse", nodes.refuse)

    graph.add_edge(START, "route")
    graph.add_conditional_edges(
        "route", nodes.route_branch, {"refuse": "refuse", "describe": "describe"}
    )
    graph.add_edge("refuse", END)

    graph.add_edge("describe", "plan")
    graph.add_conditional_edges("plan", nodes.fan_out, ["gather"])
    graph.add_edge("gather", "detect")
    graph.add_edge("detect", "draft")
    graph.add_edge("draft", "assemble")
    graph.add_edge("assemble", END)

    return graph.compile()


def _trace_config(run_id: str, brief: str) -> dict[str, Any]:

    from ..providers.tracing import run_metadata, tracing_enabled

    if not tracing_enabled():
        return {}
    return {
        "run_name": brief[:80],
        "metadata": run_metadata(run_id, brief),
        "tags": ["screening-note"],
    }


async def run_brief(brief: str, run_id: str | None = None) -> dict[str, Any]:
    """Produce a screening note for one brief."""
    run_id = run_id or uuid.uuid4().hex[:12]
    log.info("run.start", run_id=run_id, brief=brief)

    final = await build().ainvoke(
        initial_state(run_id=run_id, brief=brief), config=_trace_config(run_id, brief)
    )

    log.info(
        "run.end",
        run_id=run_id,
        refused=bool((final.get("note") or {}).get("refusal")),
        tool_calls=len(final.get("tool_calls", [])),
        dropped_claims=len(final.get("dropped_claims", [])),
    )
    return final

STAGE_LABELS: dict[str, str] = {
    "route": "Routing the brief",
    "describe": "Reading what the corpus contains",
    "plan": "Planning the note",
    "gather": "Gathering evidence",
    "detect": "Checking for conflicts and gaps",
    "draft": "Drafting claims",
    "assemble": "Verifying citations and assembling",
    "refuse": "Declining the brief",
}


async def stream_brief(brief: str, run_id: str | None = None):
    """Run a brief, yielding an event per completed node.

    Events are ``(kind, payload)``. The caller decides how to deliver them; the API
    turns them into server-sent events.
    """
    run_id = run_id or uuid.uuid4().hex[:12]
    log.info("run.start", run_id=run_id, brief=brief)
    yield "run_started", {"run_id": run_id, "brief": brief}

    state: dict[str, Any] = {}
    seen_tool_calls = 0

    async for update in build().astream(
        initial_state(run_id=run_id, brief=brief),
        stream_mode="updates",
        config=_trace_config(run_id, brief),
    ):
        for node, delta in update.items():
            if not isinstance(delta, dict):
                continue
            state.update({k: v for k, v in delta.items() if k not in ("tool_calls",)})
            yield "stage", {"node": node, "label": STAGE_LABELS.get(node, node)}

            if node == "route" and delta.get("refusal"):
                yield "refusal", delta["refusal"]
            if node == "plan" and delta.get("plan"):
                yield "plan", delta["plan"]
            if node == "detect":
                yield "detected", {
                    "conflicts": delta.get("conflicts", []),
                    "gaps": delta.get("gaps", []),
                }

            calls = delta.get("tool_calls") or []
            for call in calls:
                seen_tool_calls += 1
                yield "tool_call", call

            if node in ("assemble", "refuse") and delta.get("note"):
                state["note"] = delta["note"]
                for key in ("dropped_claims", "violations"):
                    if delta.get(key):
                        state[key] = delta[key]

    note = state.get("note")
    log.info("run.end", run_id=run_id, refused=bool((note or {}).get("refusal")),
             tool_calls=seen_tool_calls)
    yield "note", {
        "note": note,
        "dropped_claims": state.get("dropped_claims", []),
        "violations": state.get("violations", []),
    }
    yield "done", {"run_id": run_id}
