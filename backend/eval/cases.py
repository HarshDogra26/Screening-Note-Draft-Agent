"""Evaluation case schema and loader.
"""

from __future__ import annotations
from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict, Field

CASES_DIR = Path(__file__).resolve().parent / "cases"


class Expect(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # --- refusal -----------------------------------------------------------
    refusal: bool = False
    refusal_reason: str | None = None

    # --- grounding ---------------------------------------------------------
    must_cite: tuple[str, ...] = ()
    must_cite_any: tuple[str, ...] = ()
    must_not_cite: tuple[str, ...] = ()
    all_claims_cited: bool = True

    # --- content -----------------------------------------------------------
    must_contain: tuple[str, ...] = ()
    must_not_contain: tuple[str, ...] = ()
    forbidden_numbers: tuple[str, ...] = ()

    # --- structure ---------------------------------------------------------
    conflict_about: tuple[str, ...] = ()
    conflict_positions: tuple[str, ...] = ()
    gap_about: tuple[str, ...] = ()
    gap_reason: str | None = None

    # --- tool use ----------------------------------------------------------
    tools_used: tuple[str, ...] = ()
    min_sections: int = 0
    min_claims: int = 0


class Case(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    brief: str
    domain: str  # part_a | part_b | refusal
    requirement: tuple[str, ...]
    why: str
    expect: Expect = Field(default_factory=Expect)
    known_failure: str | None = None


def load_cases(directory: Path | None = None) -> list[Case]:
    directory = directory or CASES_DIR
    cases: list[Case] = []
    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cases.extend(Case(**c) for c in raw.get("cases", []))
    seen = [c.id for c in cases]
    duplicates = {i for i in seen if seen.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate case ids: {sorted(duplicates)}")
    return sorted(cases, key=lambda c: c.id)


def validate(cases: list[Case]) -> list[str]:
    """Check every cited source in the expectations actually exists.
    """
    from app.api.sources import resolve

    problems: list[str] = []
    for case in cases:
        for token in (
            *case.expect.must_cite,
            *case.expect.must_cite_any,
            *case.expect.must_not_cite,
        ):
            resolved = resolve(token)
            if not resolved.resolved:
                problems.append(f"{case.id}: expectation cites {token!r} — {resolved.error}")
        if case.expect.refusal and not case.expect.refusal_reason:
            problems.append(f"{case.id}: expects a refusal but names no reason code")
        if case.domain not in {"part_a", "part_b", "refusal"}:
            problems.append(f"{case.id}: unknown domain {case.domain!r}")
    return problems
