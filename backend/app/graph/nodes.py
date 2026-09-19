"""Graph nodes."""

from __future__ import annotations
import json
from datetime import date
from pathlib import Path
from typing import Any
from ..config import get_settings
from ..domain.enums import ClaimStatus, Domain, GapReason, RefusalReason, SectionKind
from ..domain.note import Claim, Gap, Refusal, ScreeningNote, Section
from ..domain.plan import NotePlan, RouteDecision
from ..logging import get_logger
from ..providers.azure_llm import get_llm
from ..services import domain_guard
from ..services.conflict_detector import detect_capacity_conflicts
from ..services.grounding import CitationResolver, verify_claims
from .schemas import DraftedSection, GatherDecision
from .state import NoteState
from .tools import session

log = get_logger(__name__)
PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def _json(value: Any, limit: int = 60_000) -> str:
    text = json.dumps(value, indent=2, sort_keys=True, default=str)
    return text if len(text) <= limit else text[:limit] + "\n... [truncated]"


# --- 1. route ---------------------------------------------------------------


def temporal_context() -> str:

    from ..services.datasets import prices

    months = sorted({r.month for r in prices()})
    today = date.today().isoformat()
    return (
        f"Today's date is {today}. The price assessments cover {months[0]} to "
        f"{months[-1]}, and the documents span 2025-09 to 2026-08. Every month in "
        "that range is HISTORICAL and can be reported. A brief is only asking for a "
        f"forecast if it asks about a period after {months[-1]}."
    )


def entity_context(brief: str) -> str:

    from ..services.datasets import plants
    from ..services.retrieval import part_b_index

    lowered = brief.lower()
    part_a_terms: set[str] = set()
    for plant in plants():
        for term in (plant.company, plant.plant_name, plant.city):
            if term.lower() in lowered:
                part_a_terms.add(term)

    _, companies, _, _ = part_b_index()
    part_b_terms = {
        code for code, basis in companies.items()
        if code.lower() in lowered or basis.legal_entity.lower().split()[0] in lowered
    }

    lines = [
        "Entity check, matched against the datasets themselves rather than from "
        "general knowledge:",
        f"  named in the synthetic register (PART_A): {sorted(part_a_terms) or 'none'}",
        f"  named in the real filings (PART_B): {sorted(part_b_terms) or 'none'}",
    ]
    if part_a_terms and not part_b_terms:
        lines.append(
            "  -> These names belong to the synthetic register. Route PART_A, even if "
            "a name also exists in the real world."
        )
    elif part_b_terms and not part_a_terms:
        lines.append("  -> Route PART_B.")
    elif part_a_terms and part_b_terms:
        lines.append("  -> Names from BOTH datasets appear. This is a cross-domain brief.")
    return "\n".join(lines)


async def route(state: NoteState) -> dict[str, Any]:
    decision = await get_llm().structured(
        label="route",
        system=prompt("router"),
        user=(
            f"{temporal_context()}\n\n"
            f"{entity_context(state['brief'])}\n\n"
            f"Brief: {state['brief']}"
        ),
        schema=RouteDecision,
    )
    log.info(
        "route.decided",
        domain=decision.domain.value if decision.domain else None,
        out_of_scope=decision.out_of_scope,
        both=decision.requires_both_domains,
    )

    if decision.out_of_scope:
        reason = (
            RefusalReason.CROSS_DOMAIN
            if decision.requires_both_domains
            else RefusalReason.OUT_OF_CORPUS
        )
        detail = decision.out_of_scope_reason or "This brief cannot be answered from the corpus."
        if decision.requires_both_domains:
            detail += (
                " The synthetic plant-and-price dataset and the real company filings are "
                "never combined in one note."
            )
        return {
            "domain": decision.domain.value if decision.domain else None,
            "route_reasoning": decision.reasoning,
            "refusal": Refusal(reason=reason, detail=detail).model_dump(mode="json"),
        }

    return {
        "domain": (decision.domain or Domain.PART_A).value,
        "route_reasoning": decision.reasoning,
        "refusal": None,
    }


def route_branch(state: NoteState) -> str:
    return "refuse" if state.get("refusal") else "describe"


# --- 2. describe (deterministic) --------------------------------------------


async def describe(state: NoteState) -> dict[str, Any]:
    """Load the corpus description before planning, so the plan is made against what
    actually exists rather than against what the model assumes exists."""
    domain = Domain(state["domain"])
    section = "prices" if domain is Domain.PART_A else "filings"

    async with session() as tools:
        full = await tools.call("describe_corpus", {"section": "all"})
        calls = tools.calls

    keep = (
        ["register", "price_series", "price_series_note", "documents"]
        if domain is Domain.PART_A
        else ["filings", "filings_note"]
    )
    corpus = {k: v for k, v in full.items() if k in keep}
    log.info("describe.loaded", domain=domain.value, sections=list(corpus), primary=section)
    # Reported like any other tool call: requirement 2 asks for the agent's tool use to
    # be visible, and this one is made on its behalf.
    return {"corpus": corpus, "tool_calls": calls}


# --- 3. plan ----------------------------------------------------------------


async def plan(state: NoteState) -> dict[str, Any]:
    settings = get_settings()
    note_plan = await get_llm().structured(
        label="plan",
        system=prompt("planner"),
        user=(
            f"{temporal_context()}\n\n"
            f"Brief: {state['brief']}\n"
            f"Dataset: {state['domain']}\n\n"
            f"What the corpus contains:\n{_json(state['corpus'])}"
        ),
        schema=NotePlan,
    )

    sections = note_plan.sections[: settings.max_sections]
    payload = note_plan.model_copy(update={"sections": sections}).model_dump(mode="json")

    # Requirement 1: the plan is emitted before any retrieval, and is visible.
    log.info(
        "plan.emitted",
        sections=[s.id for s in sections],
        kinds=[s.kind.value for s in sections],
        tools=sorted({t for s in sections for t in s.intended_tools}),
        excluded=[e.topic for e in note_plan.excluded],
    )
    return {"plan": payload}


def fan_out(state: NoteState) -> list:
    """One gather branch per section."""
    from langgraph.types import Send

    return [
        Send("gather", {"section": section, "shared": dict(state)})
        for section in (state.get("plan") or {}).get("sections", [])
    ]


# --- 4. gather --------------------------------------------------------------


async def gather(payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    section = payload["section"]
    state = payload["shared"]
    section_id = section["id"]

    collected: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    async with session(domain=state["domain"]) as tools:
        signatures = await tools.describe_tools()
        for step in range(settings.max_tool_calls_per_section):
            decision = await get_llm().structured(
                label=f"gather.{section_id}",
                system=prompt("gather"),
                user=(
                    f"Brief: {state['brief']}\n"
                    f"Dataset: {state['domain']}\n\n"
                    f"Tool signatures — use these argument names exactly:\n"
                    f"{signatures}\n\n"
                    f"Section: {section['title']} ({section['kind']})\n"
                    f"Questions: {json.dumps(section['questions'])}\n"
                    f"Planned tools: {json.dumps(section['intended_tools'])}\n"
                    f"Unanswerable if: {section['unanswerable_if']}\n\n"
                    f"Corpus description:\n{_json(state['corpus'], 20_000)}\n\n"
                    f"Evidence gathered so far ({len(collected)} results):\n"
                    f"{_json(collected, 40_000)}\n\n"
                    f"Tool call {step + 1} of at most {settings.max_tool_calls_per_section}."
                ),
                schema=GatherDecision,
            )

            if decision.action == "done" or decision.tool_call is None:
                log.info("gather.done", section=section_id, steps=step, reason=decision.reason)
                break

            result = await tools.call(decision.tool_call.tool, decision.tool_call.arguments)
            collected.append(
                {
                    "tool": decision.tool_call.tool,
                    "arguments": decision.tool_call.arguments,
                    "why": decision.tool_call.why,
                    "result": result,
                }
            )
        calls = tools.calls

    return {"evidence": {section_id: collected}, "tool_calls": calls}


# --- 5. detect (deterministic) ----------------------------------------------


async def detect(state: NoteState) -> dict[str, Any]:

    if Domain(state["domain"]) is not Domain.PART_A:
        return {"conflicts": [], "gaps": []}

    from ..services.datasets import plants
    from ingestion.part_a_docs import load_documents

    conflicts = detect_capacity_conflicts(load_documents(), plants())

    gaps: list[Gap] = []
    for plant in plants():
        if plant.capacity_kta is None:
            gaps.append(
                Gap(
                    subject=f"{plant.plant_name} ({plant.company}) capacity",
                    reason=GapReason.NOT_PUBLICLY_CONFIRMED,
                    detail=(
                        f"The register carries no capacity for {plant.plant_name}; its "
                        "source_type is 'Not publicly confirmed'. No estimate is made and "
                        "the unit is excluded from totals rather than counted as zero."
                    ),
                    citations=[{"kind": "plant", "plant_id": plant.plant_id}],
                )
            )

    log.info("detect.done", conflicts=len(conflicts), gaps=len(gaps))
    return {
        "conflicts": [c.model_dump(mode="json") for c in conflicts],
        "gaps": [g.model_dump(mode="json") for g in gaps],
    }


# --- 6. draft ---------------------------------------------------------------


async def draft(state: NoteState) -> dict[str, Any]:
    sections = (state.get("plan") or {}).get("sections", [])
    evidence = state.get("evidence", {})
    drafts: dict[str, Any] = {}

    for section in sections:
        section_id = section["id"]
        drafted = await get_llm().structured(
            label=f"draft.{section_id}",
            system=prompt("draft"),
            user=(
                f"Brief: {state['brief']}\n\n"
                f"Section: {section['title']} ({section['kind']})\n"
                f"Questions this section must answer: {json.dumps(section['questions'])}\n\n"
                f"Evidence retrieved for this section:\n"
                f"{_json(evidence.get(section_id, []))}\n\n"
                "Write the claims for this section. Every number must appear in the "
                "evidence above."
            ),
            schema=DraftedSection,
        )
        drafts[section_id] = drafted.model_dump(mode="json")

    return {"drafts": drafts}


# --- 7. verify + assemble (deterministic) -----------------------------------


async def assemble(state: NoteState) -> dict[str, Any]:
    domain = Domain(state["domain"])
    plan_payload = state.get("plan") or {}
    evidence = state.get("evidence", {})
    drafts = state.get("drafts", {})
    resolver = CitationResolver()

    sections: list[Section] = []
    dropped_records: list[dict[str, Any]] = []
    violations: list[Any] = []

    # Sorted by plan order, never by whichever branch finished first.
    for section_plan in plan_payload.get("sections", []):
        section_id = section_plan["id"]
        drafted = drafts.get(section_id) or {}

        claims: list[Claim] = []
        uncited = 0
        for c in drafted.get("claims", []):
            if not c.get("citations"):
                # Dropped here rather than rejected at the model's schema boundary,
                # so one uncited claim costs a claim instead of the whole run.
                uncited += 1
                dropped_records.append(
                    {
                        "section": section_id,
                        "text": c.get("text", ""),
                        "unresolved_citations": [],
                        "unsupported_numbers": [],
                        "reason": "claim carried no citation",
                    }
                )
                log.warning("verify.uncited_claim_dropped", section=section_id,
                            text=c.get("text", "")[:160])
                continue
            claims.append(
                Claim(
                    text=c["text"],
                    citations=c["citations"],
                    status=ClaimStatus(c.get("status", ClaimStatus.ASSERTED)),
                )
            )

        in_domain: list[Claim] = []
        for claim in claims:
            violation = domain_guard.check_claim(claim, domain, where=section_id)
            if violation is None:
                in_domain.append(claim)
            else:
                violations.append(violation)
                log.warning(
                    "guard.cross_domain_claim_dropped",
                    section=section_id,
                    domain=domain.value,
                    citations=list(violation.offending_citations),
                )

        kept, dropped = verify_claims(in_domain, evidence.get(section_id, []), resolver)
        for claim, result in dropped:
            dropped_records.append(
                {
                    "section": section_id,
                    "text": claim.text,
                    "unresolved_citations": list(result.unresolved_citations),
                    "unsupported_numbers": list(result.unsupported_numbers),
                }
            )

        notes = [drafted.get("coverage_note")]
        if uncited:
            notes.append(f"{uncited} claim(s) were removed because they carried no citation.")
        n_cross_domain = len(claims) - len(in_domain)
        if n_cross_domain:
            notes.append(
                f"{n_cross_domain} claim(s) were removed because they drew on the other "
                "dataset; Part A (synthetic) and Part B (real company filings) are never "
                "combined in one note."
            )
        if dropped:
            notes.append(
                f"{len(dropped)} claim(s) were removed because a figure or citation could "
                "not be traced to the retrieved evidence."
            )
        note_text = " ".join(n for n in notes if n) or None

        sections.append(
            Section(
                id=section_id,
                kind=SectionKind(section_plan["kind"]),
                title=section_plan["title"],
                claims=kept,
                gaps=[Gap(**g) for g in drafted.get("gaps", [])],
                coverage_note=note_text,
            )
        )

    manifest = _manifest()
    note = ScreeningNote(
        run_id=state["run_id"],
        brief=state["brief"],
        domain=domain,
        plan=plan_payload,
        sections=sections,
        conflicts=state.get("conflicts", []),
        gaps=state.get("gaps", []),
        index_manifest=manifest,
        tool_calls=state.get("tool_calls", []),
    )

    log.info(
        "note.assembled",
        sections=len(sections),
        claims=len(note.all_claims),
        dropped=len(dropped_records),
        violations=len(violations),
        conflicts=len(note.conflicts),
    )
    return {
        "note": note.model_dump(mode="json"),
        "dropped_claims": dropped_records,
        "violations": [v.model_dump(mode="json") for v in violations],
        "index_manifest": manifest,
    }


async def refuse(state: NoteState) -> dict[str, Any]:
    """A refusal is a valid, structured outcome -- not an error."""
    refusal = Refusal(**state["refusal"])
    note = ScreeningNote(
        run_id=state["run_id"],
        brief=state["brief"],
        domain=Domain(state["domain"]) if state.get("domain") else Domain.PART_A,
        refusal=refusal,
        index_manifest=_manifest(),
    )
    log.info("note.refused", reason=refusal.reason.value, detail=refusal.detail[:200])
    return {"note": note.model_dump(mode="json")}


def _manifest() -> dict[str, Any]:
    """Stamp the note with the corpus it was produced from."""
    path = get_settings().index_dir / "manifest.json"
    if not path.exists():
        return {"status": "no manifest; run `python -m ingestion.cli manifest`"}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "built_at": data.get("built_at"),
        "ingest_version": data.get("ingest_version"),
        "chunker_version": data.get("chunker_version"),
        "counts": data.get("counts"),
    }
