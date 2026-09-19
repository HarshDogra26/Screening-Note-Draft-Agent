"""The orchestrator, end to end, with a scripted model.

No Azure, no network. The MCP calls are real protocol calls over FastMCP's in-memory
transport, so the tools, their schemas and the deterministic layer are all genuinely
exercised — only the model is faked.

The point of these tests is the parts the model does NOT control: that a fabricated
number is dropped even when the model insists on it, that a cross-domain citation is
removed, that the plan exists before any retrieval, and that section order is fixed.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.domain.enums import Domain
from app.domain.plan import ExcludedTopic, NotePlan, RouteDecision, SectionPlan
from app.graph.builder import run_brief
from app.graph.schemas import DraftedClaim, DraftedSection, GatherDecision, ToolCall
from app.providers.azure_llm import ScriptedLLM, set_llm


@pytest.fixture(autouse=True)
def inprocess_mcp(monkeypatch):
    """Run the MCP server in-process; still the real protocol."""
    settings = get_settings()
    monkeypatch.setattr(settings, "mcp_inprocess", True, raising=False)
    yield
    set_llm(None)


def plan_with(*sections: SectionPlan, excluded=()) -> NotePlan:
    return NotePlan(
        brief="b", domain=Domain.PART_A, sections=list(sections), excluded=list(excluded)
    )


SUPPLY = SectionPlan(
    id="s1", kind="supply_base", title="Southeast Asia ethylene supply",
    questions=["Which operating units and what capacity?"],
    intended_tools=["query_plant_register"],
    tool_rationale="The register lists the units.",
    unanswerable_if="Capacity is not publicly confirmed for material units.",
)


def scripted(*, route: RouteDecision, note_plan: NotePlan, gather_args: dict,
             claims: dict[str, list[DraftedClaim]]) -> ScriptedLLM:
    """Build a model that plans once, makes one tool call per section, then drafts."""
    seen: set[str] = set()

    def gather_handler(user: str, schema):
        section_id = next(s.id for s in note_plan.sections if s.title in user)
        if section_id in seen:
            return GatherDecision(action="done", reason="have enough")
        seen.add(section_id)
        tool, arguments = gather_args[section_id]
        return GatherDecision(
            action="call_tool",
            tool_call=ToolCall(tool=tool, arguments=arguments, why="needed"),
            reason="gathering",
        )

    handlers = {
        "route": lambda user, schema: route,
        "plan": lambda user, schema: note_plan,
    }
    for section in note_plan.sections:
        handlers[f"gather.{section.id}"] = gather_handler
        handlers[f"draft.{section.id}"] = (
            lambda user, schema, sid=section.id: DraftedSection(claims=claims.get(sid, []))
        )
    return ScriptedLLM(handlers)


# --- refusal ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_domain_brief_is_refused_structurally():
    set_llm(ScriptedLLM({
        "route": lambda user, schema: RouteDecision(
            domain=None, reasoning="mixes both datasets",
            out_of_scope=True, requires_both_domains=True,
            out_of_scope_reason="The brief compares a synthetic operator with a real filer.",
        )
    }))
    final = await run_brief("Compare Nusantara Olefins with Reliance Industries")
    note = final["note"]

    assert note["refusal"]["reason"] == "cross_domain"
    assert "never combined" in note["refusal"]["detail"]
    assert note["sections"] == []
    assert final["tool_calls"] == [], "a refused brief must not retrieve anything"


@pytest.mark.asyncio
async def test_forecast_brief_is_refused():
    set_llm(ScriptedLLM({
        "route": lambda user, schema: RouteDecision(
            domain=Domain.PART_A, reasoning="asks for an outlook",
            out_of_scope=True,
            out_of_scope_reason="The sources state they do not publish forecasts or outlooks.",
        )
    }))
    note = (await run_brief("Outlook for Southeast Asia ethylene prices in 2027"))["note"]
    assert note["refusal"]["reason"] == "out_of_corpus"
    assert "forecast" in note["refusal"]["detail"]


# --- planning is visible and comes first -----------------------------------


@pytest.mark.asyncio
async def test_plan_is_emitted_and_carried_into_the_note():
    set_llm(scripted(
        route=RouteDecision(domain=Domain.PART_A, reasoning="plants"),
        note_plan=plan_with(
            SUPPLY,
            excluded=[ExcludedTopic(topic="price outlook",
                                    reason="The Monitor does not publish forecasts.")],
        ),
        gather_args={"s1": ("query_plant_register",
                            {"region": "Southeast Asia", "product": "Ethylene",
                             "status": ["Operating"], "aggregate": True})},
        claims={"s1": []},
    ))
    final = await run_brief("Southeast Asia ethylene supply")
    plan = final["note"]["plan"]

    assert [s["id"] for s in plan["sections"]] == ["s1"]
    assert plan["sections"][0]["tool_rationale"]
    assert plan["sections"][0]["unanswerable_if"]
    assert plan["excluded"][0]["topic"] == "price outlook"


@pytest.mark.asyncio
async def test_tools_are_actually_called_over_mcp():
    set_llm(scripted(
        route=RouteDecision(domain=Domain.PART_A, reasoning="plants"),
        note_plan=plan_with(SUPPLY),
        gather_args={"s1": ("query_plant_register",
                            {"region": "Southeast Asia", "product": "Ethylene",
                             "status": ["Operating"], "aggregate": True})},
        claims={"s1": []},
    ))
    final = await run_brief("Southeast Asia ethylene supply")

    called = [c["tool"] for c in final["tool_calls"]]
    assert "describe_corpus" in called
    assert "query_plant_register" in called

    evidence = final["evidence"]["s1"]
    assert evidence[0]["result"]["sum_capacity_kta"] == 5370.0


# --- the model does not get the last word ----------------------------------


@pytest.mark.asyncio
async def test_fabricated_number_is_dropped_even_though_the_model_claimed_it():
    """The model is told to write 9,999 kta with a genuine citation.

    The citation resolves. The number is nowhere in the retrieved evidence. The claim
    must not reach the note.
    """
    set_llm(scripted(
        route=RouteDecision(domain=Domain.PART_A, reasoning="plants"),
        note_plan=plan_with(SUPPLY),
        gather_args={"s1": ("query_plant_register",
                            {"region": "Southeast Asia", "product": "Ethylene",
                             "status": ["Operating"], "aggregate": True})},
        claims={"s1": [
            DraftedClaim(
                text="Operating ethylene capacity totals 5,370 kta.",
                citations=[{"kind": "plant", "plant_id": "PL-001"}],
            ),
            DraftedClaim(
                text="Operating ethylene capacity totals 9,999 kta.",
                citations=[{"kind": "plant", "plant_id": "PL-001"}],
            ),
        ]},
    ))
    final = await run_brief("Southeast Asia ethylene supply")

    texts = [c["text"] for c in final["note"]["sections"][0]["claims"]]
    assert any("5,370" in t for t in texts)
    assert not any("9,999" in t for t in texts)

    dropped = final["dropped_claims"]
    assert len(dropped) == 1
    assert "9,999" in dropped[0]["unsupported_numbers"]
    assert "could not be traced" in final["note"]["sections"][0]["coverage_note"]


@pytest.mark.asyncio
async def test_cross_domain_citation_is_removed_from_a_part_a_note():
    set_llm(scripted(
        route=RouteDecision(domain=Domain.PART_A, reasoning="plants"),
        note_plan=plan_with(SUPPLY),
        gather_args={"s1": ("query_plant_register", {"plant_ids": ["PL-001"]})},
        claims={"s1": [
            DraftedClaim(
                text="Revenue was 152,441 million.",
                citations=[{"kind": "filing", "company": "PETRONAS",
                            "filename": "Financial Report 1H 2025.pdf", "page": 5}],
            ),
        ]},
    ))
    final = await run_brief("Southeast Asia ethylene supply")

    assert final["note"]["sections"][0]["claims"] == []
    assert final["violations"], "a cross-domain citation must be recorded, not silently dropped"


@pytest.mark.asyncio
async def test_invented_citation_is_dropped():
    set_llm(scripted(
        route=RouteDecision(domain=Domain.PART_A, reasoning="plants"),
        note_plan=plan_with(SUPPLY),
        gather_args={"s1": ("query_plant_register", {"plant_ids": ["PL-001"]})},
        claims={"s1": [
            DraftedClaim(
                text="A unit exists at Somewhere.",
                citations=[{"kind": "plant", "plant_id": "PL-999"}],
            ),
        ]},
    ))
    final = await run_brief("Southeast Asia ethylene supply")
    assert final["note"]["sections"][0]["claims"] == []
    assert "plant:PL-999" in final["dropped_claims"][0]["unresolved_citations"]


# --- deterministic detection runs regardless of the model -------------------


@pytest.mark.asyncio
async def test_cilegon_conflict_is_attached_without_the_model_finding_it():
    """The model was never asked about conflicts. It is surfaced anyway."""
    set_llm(scripted(
        route=RouteDecision(domain=Domain.PART_A, reasoning="plants"),
        note_plan=plan_with(SUPPLY),
        gather_args={"s1": ("query_plant_register", {"plant_ids": ["PL-001"]})},
        claims={"s1": []},
    ))
    note = (await run_brief("Southeast Asia ethylene supply"))["note"]

    conflict = next(c for c in note["conflicts"] if "Cilegon Cracker 1" in c["subject"])
    assert {p["value"] for p in conflict["positions"]} == {"1,200 kta", "1,050 kta"}
    assert "does not resolve" in conflict["note"]


@pytest.mark.asyncio
async def test_blank_capacity_is_reported_as_a_gap():
    set_llm(scripted(
        route=RouteDecision(domain=Domain.PART_A, reasoning="plants"),
        note_plan=plan_with(SUPPLY),
        gather_args={"s1": ("query_plant_register", {"plant_ids": ["PL-008"]})},
        claims={"s1": []},
    ))
    note = (await run_brief("Southeast Asia ethylene supply"))["note"]

    gap = next(g for g in note["gaps"] if "Sungai Liang Cracker" in g["subject"])
    assert gap["reason"] == "not_publicly_confirmed"
    assert "rather than counted as zero" in gap["detail"]


# --- ordering and stamping --------------------------------------------------


@pytest.mark.asyncio
async def test_sections_follow_plan_order_not_completion_order():
    second = SectionPlan(
        id="s2", kind="price_movement", title="H1 2026 price movements",
        questions=["How did assessments move?"],
        intended_tools=["query_price_series"],
        tool_rationale="The series shows the shape.",
        unanswerable_if="The range crosses a basis change.",
    )
    set_llm(scripted(
        route=RouteDecision(domain=Domain.PART_A, reasoning="plants and prices"),
        note_plan=plan_with(SUPPLY, second),
        gather_args={
            "s1": ("query_plant_register", {"plant_ids": ["PL-001"]}),
            "s2": ("query_price_series", {"product": "Ethylene",
                                          "region": "Southeast Asia CFR",
                                          "month_from": "2026-01", "month_to": "2026-06"}),
        },
        claims={"s1": [], "s2": []},
    ))
    final = await run_brief("Supply and prices")
    assert [s["id"] for s in final["note"]["sections"]] == ["s1", "s2"]


@pytest.mark.asyncio
async def test_note_is_stamped_with_the_corpus_it_came_from():
    set_llm(scripted(
        route=RouteDecision(domain=Domain.PART_A, reasoning="plants"),
        note_plan=plan_with(SUPPLY),
        gather_args={"s1": ("query_plant_register", {"plant_ids": ["PL-001"]})},
        claims={"s1": []},
    ))
    note = (await run_brief("Southeast Asia ethylene supply"))["note"]
    manifest = note["index_manifest"]
    assert manifest.get("counts", {}).get("plants") == 52 or "no manifest" in str(manifest)
