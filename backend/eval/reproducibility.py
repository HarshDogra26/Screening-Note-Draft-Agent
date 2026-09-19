"""Measuring whether the same brief twice gives substantially the same note.
"""

from __future__ import annotations
import re
from collections.abc import Sequence
from pydantic import BaseModel, ConfigDict
from .assertions import collect_tokens, note_text

NUMBER_RE = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})*(?:\.\d+)?(?![\w])")


class Stability(BaseModel):
    model_config = ConfigDict(frozen=True)

    brief: str
    runs: int
    section_jaccard: float
    citation_jaccard: float
    numeric_exact_match: float
    conflicts_identical: bool
    gaps_identical: bool
    tool_sequence_identical: bool
    numbers_only_in_some_runs: tuple[str, ...] = ()

    @property
    def substantially_same(self) -> bool:
        """The bar we hold ourselves to, stated explicitly rather than implied."""
        return (
            self.numeric_exact_match == 1.0
            and self.citation_jaccard >= 0.95
            and self.section_jaccard == 1.0
            and self.conflicts_identical
            and self.gaps_identical
        )


def _jaccard(sets: Sequence[set]) -> float:
    if not sets:
        return 1.0
    union = set().union(*sets)
    if not union:
        return 1.0
    intersection = set(sets[0]).intersection(*sets)
    return len(intersection) / len(union)


def _numbers(note: dict) -> set[str]:
    return set(NUMBER_RE.findall(note_text(note)))


def _sections(note: dict) -> set[str]:
    return {s.get("kind", "") for s in note.get("sections", [])}


def _conflict_key(note: dict) -> tuple:
    return tuple(
        sorted(
            (c["subject"], tuple(sorted(p["value"] for p in c["positions"])))
            for c in note.get("conflicts", [])
        )
    )


def _gap_key(note: dict) -> tuple:
    return tuple(sorted((g["subject"], g["reason"]) for g in note.get("gaps", [])))


def _tool_sequence(note: dict) -> tuple:
    return tuple(call.get("tool") for call in note.get("tool_calls", []))


def compare(brief: str, notes: Sequence[dict]) -> Stability:
    section_sets = [_sections(n) for n in notes]
    citation_sets = [collect_tokens(n) for n in notes]
    number_sets = [_numbers(n) for n in notes]

    shared_numbers = set(number_sets[0]).intersection(*number_sets) if number_sets else set()
    all_numbers = set().union(*number_sets) if number_sets else set()
    unstable = sorted(all_numbers - shared_numbers)

    return Stability(
        brief=brief,
        runs=len(notes),
        section_jaccard=round(_jaccard(section_sets), 4),
        citation_jaccard=round(_jaccard(citation_sets), 4),
        numeric_exact_match=round(len(shared_numbers) / len(all_numbers), 4) if all_numbers else 1.0,
        conflicts_identical=len({_conflict_key(n) for n in notes}) <= 1,
        gaps_identical=len({_gap_key(n) for n in notes}) <= 1,
        tool_sequence_identical=len({_tool_sequence(n) for n in notes}) <= 1,
        numbers_only_in_some_runs=tuple(unstable[:20]),
    )
