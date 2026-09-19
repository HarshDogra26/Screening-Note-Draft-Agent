"""Evaluation CLI.

    python -m eval.run validate        check the expectations against the corpus
    python -m eval.run list            show the suite
    python -m eval.run cases           run every case (needs LLM or cassettes)
    python -m eval.run cases --id A06  run one
    python -m eval.run repro --n 3     reproducibility experiment

"""

from __future__ import annotations
import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from app.config import get_settings
from app.logging import configure_logging, get_logger
from .assertions import CaseResult, check
from .cases import Case, load_cases, validate
from .reproducibility import compare

log = get_logger("eval")
REPORT_DIR = Path(__file__).resolve().parent / "reports"

REPRO_BRIEFS = [
    "Screening note on Southeast Asia ethylene supply and H1 2026 price moves",
    "What is the ethylene capacity of Cilegon Cracker 1?",
    "How has Southeast Asia propylene moved since 2024?",
]


def cmd_validate() -> int:
    cases = load_cases()
    problems = validate(cases)
    print(f"{len(cases)} cases loaded")
    by_domain: dict[str, int] = {}
    for case in cases:
        by_domain[case.domain] = by_domain.get(case.domain, 0) + 1
    print(f"  by domain: {by_domain}")
    print(f"  expecting a refusal: {sum(1 for c in cases if c.expect.refusal)}")
    print(f"  flagged as known failures: {sum(1 for c in cases if c.known_failure)}")

    if problems:
        print(f"\nFAILED: {len(problems)} expectation(s) reference sources that do not exist")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nOK: every source named in an expectation resolves against the corpus")
    return 0


def cmd_list() -> int:
    for case in load_cases():
        flag = "  [known failure]" if case.known_failure else ""
        print(f"{case.id}  {case.domain:<8}  {case.brief}{flag}")
        print(f"        requirement: {', '.join(case.requirement)}")
    return 0


async def _run_case(case: Case) -> CaseResult:
    from app.graph.builder import run_brief

    try:
        final = await run_brief(case.brief)
    except Exception as exc:  # noqa: BLE001
        return CaseResult(
            case_id=case.id, brief=case.brief, passed=False, checks=(),
            known_failure=case.known_failure, error=f"{type(exc).__name__}: {exc}"[:400],
        )
    note = final.get("note") or {}
    return check(case, note)


async def _run_all(cases: list[Case]) -> list[CaseResult]:
    return [await _run_case(case) for case in cases]


def cmd_cases(case_id: str | None) -> int:
    cases = [c for c in load_cases() if case_id is None or c.id == case_id]
    if not cases:
        print(f"no case with id {case_id!r}")
        return 1

    results = asyncio.run(_run_all(cases))

    passed = [r for r in results if r.passed]
    expected_failures = [r for r in results if r.expected_failure]
    unexpected = [r for r in results if not r.passed and not r.known_failure]

    print()
    for result in results:
        if result.passed:
            mark = "PASS"
        elif result.known_failure:
            mark = "XFAIL"
        else:
            mark = "FAIL"
        print(f"{mark:<6} {result.case_id}  {result.brief[:66]}")
        if result.error:
            print(f"       error: {result.error}")
        for failure in result.failures:
            print(f"       - {failure.name}"
                  + (f" ({failure.detail})" if failure.detail else ""))

    print(
        f"\n{len(passed)} passed, {len(unexpected)} failed, "
        f"{len(expected_failures)} expected failures, of {len(results)}"
    )

    report = _write_report(results)
    print(f"report: {report}")
    return 1 if unexpected else 0


def cmd_repro(n: int) -> int:
    from app.graph.builder import run_brief

    async def go():
        out = []
        for brief in REPRO_BRIEFS:
            notes = []
            for _ in range(n):
                final = await run_brief(brief)
                notes.append(final.get("note") or {})
            out.append(compare(brief, notes))
        return out

    stats = asyncio.run(go())

    print(f"\nreproducibility over {n} runs per brief\n")
    for s in stats:
        verdict = "STABLE" if s.substantially_same else "VARIES"
        print(f"{verdict:<7} {s.brief[:64]}")
        print(f"        numeric claims identical : {s.numeric_exact_match:.0%}")
        print(f"        citation set jaccard     : {s.citation_jaccard:.2f}")
        print(f"        section set jaccard      : {s.section_jaccard:.2f}")
        print(f"        conflicts identical      : {s.conflicts_identical}")
        print(f"        gaps identical           : {s.gaps_identical}")
        print(f"        tool order identical     : {s.tool_sequence_identical}")
        if s.numbers_only_in_some_runs:
            print(f"        unstable figures         : {list(s.numbers_only_in_some_runs)}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "reproducibility.json"
    path.write_text(
        json.dumps([s.model_dump() for s in stats], indent=2), encoding="utf-8"
    )
    print(f"\nreport: {path}")
    return 0 if all(s.substantially_same for s in stats) else 1


def _write_report(results: list[CaseResult]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "cases.json"
    path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "provider_mode": get_settings().provider_mode.value,
                "results": [r.model_dump() for r in results],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval")
    parser.add_argument("command", choices=["validate", "list", "cases", "repro"])
    parser.add_argument("--id", default=None, help="run a single case")
    parser.add_argument("--n", type=int, default=3, help="runs per brief for repro")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    if args.command == "validate":
        return cmd_validate()
    if args.command == "list":
        return cmd_list()
    if args.command == "cases":
        return cmd_cases(args.id)
    return cmd_repro(args.n)


if __name__ == "__main__":
    sys.exit(main())
