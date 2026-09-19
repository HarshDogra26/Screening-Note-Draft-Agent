"""Graph state.
"""

from __future__ import annotations
import operator
from typing import Annotated, Any, TypedDict


def merge_by_section(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {**left, **right}


class NoteState(TypedDict, total=False):
    run_id: str
    brief: str

    # routing
    domain: str | None
    route_reasoning: str
    refusal: dict[str, Any] | None

    # planning (emitted before any retrieval - requirement 1)
    corpus: dict[str, Any]
    plan: dict[str, Any] | None

    # per-section, written concurrently
    evidence: Annotated[dict[str, Any], merge_by_section]
    drafts: Annotated[dict[str, Any], merge_by_section]

    # deterministic detection
    conflicts: list[dict[str, Any]]
    gaps: list[dict[str, Any]]

    # output
    note: dict[str, Any] | None
    index_manifest: dict[str, Any]

    # observability
    tool_calls: Annotated[list[dict[str, Any]], operator.add]
    dropped_claims: Annotated[list[dict[str, Any]], operator.add]
    violations: Annotated[list[dict[str, Any]], operator.add]


def initial_state(*, run_id: str, brief: str) -> NoteState:
    return NoteState(
        run_id=run_id,
        brief=brief,
        domain=None,
        route_reasoning="",
        refusal=None,
        corpus={},
        plan=None,
        evidence={},
        drafts={},
        conflicts=[],
        gaps=[],
        note=None,
        index_manifest={},
        tool_calls=[],
        dropped_claims=[],
        violations=[],
    )
