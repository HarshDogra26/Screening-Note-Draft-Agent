"""Mechanical assertions over a produced note.
"""

from __future__ import annotations
import re
from pydantic import BaseModel, ConfigDict
from app.domain.citation import (
    DocumentCitation,
    FilingCitation,
    PlantCitation,
    PriceCitation,
)

from .cases import Case


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    passed: bool
    detail: str = ""


class CaseResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    case_id: str
    brief: str
    passed: bool
    checks: tuple[CheckResult, ...]
    known_failure: str | None = None
    error: str | None = None
    run_id: str | None = None

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]

    @property
    def expected_failure(self) -> bool:
        return bool(self.known_failure) and not self.passed


def _token(citation: dict) -> str:
    kind = citation.get("kind")
    if kind == "plant":
        return PlantCitation(**citation).token
    if kind == "price":
        return PriceCitation(**citation).token
    if kind == "document":
        return DocumentCitation(**citation).token
    if kind == "filing":
        return FilingCitation(**citation).token
    return f"{kind}:?"


def collect_tokens(note: dict) -> set[str]:
    """Every citation anywhere in the note: claims, gaps, conflicts, refusal."""
    tokens: set[str] = set()
    for section in note.get("sections", []):
        for claim in section.get("claims", []):
            tokens.update(_token(c) for c in claim.get("citations", []))
        for gap in section.get("gaps", []):
            tokens.update(_token(c) for c in gap.get("citations", []))
    for gap in note.get("gaps", []):
        tokens.update(_token(c) for c in gap.get("citations", []))
    for conflict in note.get("conflicts", []):
        for position in conflict.get("positions", []):
            tokens.update(_token(c) for c in position.get("citations", []))
    if note.get("refusal"):
        tokens.update(_token(c) for c in note["refusal"].get("citations", []))
    return tokens


def note_text(note: dict) -> str:
    """All prose a reader would see, for the few substring checks."""
    parts: list[str] = []
    if note.get("refusal"):
        parts.append(note["refusal"].get("detail", ""))
    for section in note.get("sections", []):
        parts.append(section.get("title", ""))
        parts.append(section.get("coverage_note") or "")
        for claim in section.get("claims", []):
            parts.append(claim.get("text", ""))
        for gap in section.get("gaps", []):
            parts.append(f"{gap.get('subject', '')} {gap.get('detail', '')}")
    for gap in note.get("gaps", []):
        parts.append(f"{gap.get('subject', '')} {gap.get('detail', '')}")
    for conflict in note.get("conflicts", []):
        parts.append(conflict.get("subject", ""))
        for position in conflict.get("positions", []):
            parts.append(f"{position.get('source_label','')} {position.get('value','')}")
            parts.append(position.get("stated_reason") or "")
    return "\n".join(p for p in parts if p)


def tools_used(note: dict) -> set[str]:
    return {call.get("tool") for call in note.get("tool_calls", [])}


def check(case: Case, note: dict) -> CaseResult:
    expect = case.expect
    checks: list[CheckResult] = []
    tokens = collect_tokens(note)
    text = note_text(note)
    lowered = text.lower()

    def add(name: str, passed: bool, detail: str = "") -> None:
        checks.append(CheckResult(name=name, passed=passed, detail=detail))

    # --- refusal -----------------------------------------------------------
    refusal = note.get("refusal")
    if expect.refusal:
        add("refused", refusal is not None, "no refusal object in the note")
        if refusal and expect.refusal_reason:
            add(
                f"refusal_reason == {expect.refusal_reason}",
                refusal.get("reason") == expect.refusal_reason,
                f"got {refusal.get('reason')!r}",
            )
    elif case.domain != "refusal":
        add("not refused", refusal is None,
            f"unexpectedly refused: {(refusal or {}).get('detail', '')[:160]}")

    # --- grounding ---------------------------------------------------------
    if expect.all_claims_cited:
        uncited = [
            claim.get("text", "")[:80]
            for section in note.get("sections", [])
            for claim in section.get("claims", [])
            if not claim.get("citations")
        ]
        add("every claim carries a citation", not uncited, f"{len(uncited)} uncited")

    for token in expect.must_cite:
        add(f"cites {token}", token in tokens,
            f"note cites {len(tokens)} sources, not this one")

    if expect.must_cite_any:
        hit = [t for t in expect.must_cite_any if t in tokens]
        add(
            f"cites at least one of {list(expect.must_cite_any)}",
            bool(hit),
            f"note cites {len(tokens)} sources, none of them these",
        )

    for token in expect.must_not_cite:
        add(f"does not cite {token}", token not in tokens)

    # --- content -----------------------------------------------------------
    for needle in expect.must_contain:
        add(f"mentions {needle!r}", needle.lower() in lowered)

    for needle in expect.must_not_contain:
        add(f"avoids {needle!r}", needle.lower() not in lowered)

    for number in expect.forbidden_numbers:
        present = re.search(rf"(?<![\d,.]){re.escape(number)}(?![\d,.])", text) is not None
        add(f"does not state {number}", not present,
            "a figure that is not in the corpus appears in the note")

    # --- conflicts ---------------------------------------------------------
    for subject in expect.conflict_about:
        matching = [c for c in note.get("conflicts", []) if subject.lower() in c["subject"].lower()]
        add(f"surfaces a conflict about {subject}", bool(matching))
        if matching and expect.conflict_positions:
            values = {p["value"] for c in matching for p in c["positions"]}
            for value in expect.conflict_positions:
                add(f"conflict includes position {value}", value in values,
                    f"positions present: {sorted(values)}")
            add(
                "conflict left unresolved",
                all("resolution" not in c for c in matching),
                "a resolution field appeared, which the schema should forbid",
            )

    # --- gaps --------------------------------------------------------------
    all_gaps = list(note.get("gaps", [])) + [
        g for s in note.get("sections", []) for g in s.get("gaps", [])
    ]
    for subject in expect.gap_about:
        matching = [g for g in all_gaps if subject.lower() in g.get("subject", "").lower()]
        add(f"reports a gap about {subject}", bool(matching))
        if matching and expect.gap_reason:
            add(
                f"gap reason == {expect.gap_reason}",
                any(g.get("reason") == expect.gap_reason for g in matching),
                f"got {[g.get('reason') for g in matching]}",
            )

    # --- tools and structure ----------------------------------------------
    used = tools_used(note)
    for tool in expect.tools_used:
        add(f"used {tool}", tool in used, f"tools used: {sorted(used - {None})}")

    if expect.min_sections:
        n = len(note.get("sections", []))
        add(f"at least {expect.min_sections} sections", n >= expect.min_sections, f"got {n}")

    if expect.min_claims:
        n = sum(len(s.get("claims", [])) for s in note.get("sections", []))
        add(f"at least {expect.min_claims} claims", n >= expect.min_claims, f"got {n}")

    return CaseResult(
        case_id=case.id,
        brief=case.brief,
        passed=all(c.passed for c in checks),
        checks=tuple(checks),
        known_failure=case.known_failure,
        run_id=note.get("run_id"),
    )
