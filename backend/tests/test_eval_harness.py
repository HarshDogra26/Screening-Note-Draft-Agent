"""Tests for the evaluation harness itself.

The brief says it would rather see honest failures than a passing suite. That only
means anything if the harness can actually fail, so these feed it deliberately bad
notes and assert that each check fires. A green suite from assertions that cannot
detect anything would be worse than no suite.
"""

from __future__ import annotations

import pytest

from eval.assertions import check, collect_tokens, note_text
from eval.cases import Case, Expect, load_cases, validate
from eval.reproducibility import compare


def note(**overrides) -> dict:
    base = {
        "run_id": "test",
        "brief": "b",
        "domain": "part_a",
        "sections": [],
        "conflicts": [],
        "gaps": [],
        "refusal": None,
        "tool_calls": [],
    }
    base.update(overrides)
    return base


def section(claims=(), gaps=(), **kw) -> dict:
    return {
        "id": "s1", "kind": "supply_base", "title": "Supply",
        "claims": list(claims), "gaps": list(gaps),
        "conflicts": [], "coverage_note": None, **kw,
    }


def claim(text: str, citations) -> dict:
    return {"text": text, "citations": list(citations), "status": "asserted"}


def case(**expect) -> Case:
    return Case(
        id="T01", brief="b", domain="part_a", requirement=("grounding",),
        why="test", expect=Expect(**expect),
    )


# --- the suite itself -------------------------------------------------------


def test_the_shipped_suite_loads_and_is_grounded():
    cases = load_cases()
    assert len(cases) >= 15, "the brief asks for roughly 15 cases"
    assert validate(cases) == [], "an expectation names a source that does not exist"


def test_suite_includes_refusal_cases():
    """The brief asks explicitly for cases where the right answer is a refusal."""
    cases = load_cases()
    refusals = [c for c in cases if c.domain == "refusal"]
    assert len(refusals) >= 4
    assert any(c.expect.refusal_reason == "cross_domain" for c in cases)
    assert any(c.expect.refusal_reason == "out_of_corpus" for c in cases)


def test_suite_covers_every_graded_requirement():
    covered = {r for c in load_cases() for r in c.requirement}
    assert {"planning", "tools", "grounding", "gaps", "conflicts"} <= covered


def test_validate_rejects_an_expectation_citing_a_missing_source():
    bad = Case(
        id="X", brief="b", domain="part_a", requirement=("grounding",), why="w",
        expect=Expect(must_cite=("plant:PL-999",)),
    )
    problems = validate([bad])
    assert problems and "PL-999" in problems[0]


# --- assertions detect what they claim to -----------------------------------


def test_missing_citation_is_detected():
    result = check(
        case(must_cite=("plant:PL-001",)),
        note(sections=[section([claim("Something.", [{"kind": "plant", "plant_id": "PL-002"}])])]),
    )
    assert not result.passed
    assert any("cites plant:PL-001" in f.name for f in result.failures)


def test_present_citation_passes():
    result = check(
        case(must_cite=("plant:PL-001",)),
        note(sections=[section([claim("Something.", [{"kind": "plant", "plant_id": "PL-001"}])])]),
    )
    assert result.passed


def test_price_citation_token_includes_basis():
    """A price cited on the wrong basis must not satisfy an expectation."""
    price = {"kind": "price", "product": "Propylene", "region": "Southeast Asia CFR",
             "month": "2026-04", "basis": "Spot"}
    tokens = collect_tokens(note(sections=[section([claim("x", [price])])]))
    assert "price:Propylene|Southeast Asia CFR|2026-04|Spot" in tokens
    assert "price:Propylene|Southeast Asia CFR|2026-04|Contract" not in tokens


def test_uncited_claim_is_detected():
    result = check(
        case(all_claims_cited=True),
        note(sections=[section([{"text": "Bare assertion.", "citations": [],
                                 "status": "asserted"}])]),
    )
    assert not result.passed
    assert any("every claim carries a citation" in f.name for f in result.failures)


def test_interpolated_number_is_detected():
    """The HDPE trap: a value printed for a month with no assessment is invented."""
    result = check(
        case(forbidden_numbers=("922",)),
        note(sections=[section([claim("January was around 922 USD/tonne.",
                                      [{"kind": "plant", "plant_id": "PL-001"}])])]),
    )
    assert not result.passed
    assert any("does not state 922" in f.name for f in result.failures)


def test_forbidden_number_does_not_fire_on_a_substring():
    """5,370 must not trip a rule about 37."""
    result = check(
        case(forbidden_numbers=("37",)),
        note(sections=[section([claim("Total is 5,370 kta.",
                                      [{"kind": "plant", "plant_id": "PL-001"}])])]),
    )
    assert result.passed


def test_unexpected_refusal_is_detected():
    result = check(
        case(must_cite=()),
        note(refusal={"reason": "out_of_corpus", "detail": "nope", "citations": []}),
    )
    assert not result.passed
    assert any("not refused" in f.name for f in result.failures)


def test_expected_refusal_with_wrong_reason_is_detected():
    c = Case(id="T", brief="b", domain="refusal", requirement=("grounding",), why="w",
             expect=Expect(refusal=True, refusal_reason="cross_domain"))
    result = check(c, note(refusal={"reason": "out_of_corpus", "detail": "d", "citations": []}))
    assert not result.passed
    assert any("cross_domain" in f.name for f in result.failures)


def test_conflict_with_only_one_position_is_detected():
    """Presenting one side of a disagreement is the failure being guarded against."""
    result = check(
        case(conflict_about=("Cilegon",), conflict_positions=("1,200 kta", "1,050 kta")),
        note(conflicts=[{
            "subject": "Cilegon Cracker 1 capacity",
            "positions": [{"source_label": "register", "value": "1,200 kta",
                           "stated_reason": None, "citations": []}],
            "note": "n",
        }]),
    )
    assert not result.passed
    assert any("1,050 kta" in f.name for f in result.failures)


def test_missing_conflict_entirely_is_detected():
    result = check(case(conflict_about=("Cilegon",)), note())
    assert not result.passed


def test_gap_reason_mismatch_is_detected():
    result = check(
        case(gap_about=("Sungai Liang",), gap_reason="not_publicly_confirmed"),
        note(gaps=[{"subject": "Sungai Liang Cracker capacity", "reason": "no_assessment",
                    "detail": "d", "citations": []}]),
    )
    assert not result.passed
    assert any("gap reason" in f.name for f in result.failures)


def test_tool_use_is_checked():
    result = check(
        case(tools_used=("query_price_series",)),
        note(tool_calls=[{"tool": "query_plant_register", "arguments": {}, "kind": "ok", "n": 1}]),
    )
    assert not result.passed


def test_known_failure_is_reported_as_expected_failure():
    c = Case(id="T", brief="b", domain="part_a", requirement=("grounding",), why="w",
             expect=Expect(must_cite=("plant:PL-001",)), known_failure="predicted in O3")
    result = check(c, note())
    assert not result.passed
    assert result.expected_failure is True


def test_note_text_gathers_conflicts_and_gaps():
    text = note_text(note(
        gaps=[{"subject": "S", "reason": "r", "detail": "not publicly confirmed", "citations": []}],
        conflicts=[{"subject": "C", "positions": [
            {"source_label": "Monitor", "value": "1,050 kta", "stated_reason": "why",
             "citations": []}], "note": "n"}],
    ))
    assert "not publicly confirmed" in text
    assert "1,050 kta" in text and "Monitor" in text


# --- reproducibility metrics ------------------------------------------------


def test_identical_notes_are_stable():
    n = note(sections=[section([claim("Total is 5,370 kta.",
                                      [{"kind": "plant", "plant_id": "PL-001"}])])])
    stability = compare("b", [n, n, n])
    assert stability.substantially_same
    assert stability.numeric_exact_match == 1.0


def test_a_changed_figure_makes_a_run_unstable():
    """The check that matters most: a number that moves between runs is a bug."""
    a = note(sections=[section([claim("Total is 5,370 kta.",
                                      [{"kind": "plant", "plant_id": "PL-001"}])])])
    b = note(sections=[section([claim("Total is 5,380 kta.",
                                      [{"kind": "plant", "plant_id": "PL-001"}])])])
    stability = compare("b", [a, b])
    assert not stability.substantially_same
    assert stability.numeric_exact_match < 1.0
    assert "5,380" in stability.numbers_only_in_some_runs


def test_differing_citations_lower_the_jaccard():
    a = note(sections=[section([claim("x", [{"kind": "plant", "plant_id": "PL-001"}])])])
    b = note(sections=[section([claim("x", [{"kind": "plant", "plant_id": "PL-002"}])])])
    assert compare("b", [a, b]).citation_jaccard == 0.0


def test_tool_order_variance_is_reported_but_does_not_fail_stability():
    """Ordering within a section is expected to vary and does not affect correctness."""
    a = note(tool_calls=[{"tool": "x"}, {"tool": "y"}])
    b = note(tool_calls=[{"tool": "y"}, {"tool": "x"}])
    stability = compare("b", [a, b])
    assert stability.tool_sequence_identical is False
    assert stability.substantially_same is True


@pytest.mark.parametrize("case_obj", load_cases(), ids=lambda c: c.id)
def test_every_shipped_case_is_well_formed(case_obj):
    assert case_obj.why.strip(), f"{case_obj.id} does not say why it exists"
    assert case_obj.requirement, f"{case_obj.id} maps to no graded requirement"
    if case_obj.domain == "refusal":
        expect = case_obj.expect
        assert expect.refusal or expect.must_contain or expect.forbidden_numbers, (
            f"{case_obj.id} is a refusal case but asserts nothing about the outcome"
        )
