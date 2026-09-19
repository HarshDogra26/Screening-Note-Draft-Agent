"""Grounding verification.
"""

from __future__ import annotations
import re
from collections.abc import Iterable
from typing import Any
from pydantic import BaseModel, ConfigDict
from ..domain.citation import (
    Citation,
    DocumentCitation,
    FilingCitation,
    PlantCitation,
    PriceCitation,
)
from ..domain.note import Claim
from ..logging import get_logger

log = get_logger(__name__)

NUMBER_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?![\w])")

YEAR_RE = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")
TRIVIAL_MAX = 12


class NumericFinding(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: float
    as_written: str
    supported: bool
    how: str  


class VerificationResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    ok: bool
    unresolved_citations: tuple[str, ...] = ()
    unsupported_numbers: tuple[str, ...] = ()
    findings: tuple[NumericFinding, ...] = ()

    @property
    def reason(self) -> str:
        parts = []
        if self.unresolved_citations:
            parts.append(f"citations do not resolve: {', '.join(self.unresolved_citations)}")
        if self.unsupported_numbers:
            parts.append(f"numbers not found in evidence: {', '.join(self.unsupported_numbers)}")
        return "; ".join(parts)


# --- citation resolution ---------------------------------------------------


class CitationResolver:
    """Resolves citations against the real sources. Built once, reused."""

    def __init__(self) -> None:
        from .datasets import plants, prices

        self._plant_ids = {p.plant_id for p in plants()}
        self._price_keys = {
            (r.product, r.region, r.month, r.basis) for r in prices()
        }
        self._documents: set[str] = set()
        self._filing_pages: dict[str, int] = {}

    def _load_documents(self) -> set[str]:
        if not self._documents:
            from ..config import get_settings

            self._documents = {p.name for p in get_settings().documents_dir.glob("*.md")}
        return self._documents

    def _load_filings(self) -> dict[str, int]:
        if not self._filing_pages:
            from .retrieval import part_b_index

            filings, _, _, _ = part_b_index()
            self._filing_pages = {f.filename: len(f.pages) for f in filings}
        return self._filing_pages

    def resolve(self, citation: Citation) -> bool:
        if isinstance(citation, PlantCitation):
            return citation.plant_id in self._plant_ids
        if isinstance(citation, PriceCitation):
            return (
                citation.product,
                citation.region,
                citation.month,
                citation.basis,
            ) in self._price_keys
        if isinstance(citation, DocumentCitation):
            return citation.filename in self._load_documents()
        if isinstance(citation, FilingCitation):
            pages = self._load_filings()
            if citation.filename not in pages:
                return False
            if citation.page is None:
                return True
            return 1 <= citation.page <= pages[citation.filename]
        return False

    def unresolved(self, citations: Iterable[Citation]) -> list[str]:
        return [c.token for c in citations if not self.resolve(c)]


# --- numeric provenance ----------------------------------------------------


def extract_numbers(text: str) -> list[tuple[float, str]]:
    """Every number in the text, with the literal form it was written in."""
    out: list[tuple[float, str]] = []
    for match in NUMBER_RE.finditer(text):
        whole, frac = match.group(1), match.group(2)
        raw = match.group(0)
        try:
            value = float(f"{whole.replace(',', '')}.{frac}" if frac else whole.replace(",", ""))
        except ValueError:  # pragma: no cover - regex shape makes this unreachable
            continue
        out.append((value, raw))
    return out


def collect_evidence_numbers(evidence: Any, into: set[float] | None = None) -> set[float]:
    """Every number reachable in the retrieved evidence.
    """
    found = into if into is not None else set()

    if isinstance(evidence, bool):
        return found
    if isinstance(evidence, (int, float)):
        found.add(round(float(evidence), 4))
        return found
    if isinstance(evidence, str):
        for value, _ in extract_numbers(evidence):
            found.add(round(value, 4))
        return found
    if isinstance(evidence, dict):
        for value in evidence.values():
            collect_evidence_numbers(value, found)
        return found
    if isinstance(evidence, (list, tuple, set)):
        for item in evidence:
            collect_evidence_numbers(item, found)
        return found
    return found


def _matches_evidence(value: float, evidence: set[float]) -> bool:
    """Exact, or the claim rounded a value the evidence carries.
    """
    if round(value, 4) in evidence:
        return True
    return any(
        round(known, places) == value
        for known in evidence
        for places in (0, 1, 2)
    )


def verify_claim(
    claim: Claim,
    evidence: Any,
    resolver: CitationResolver,
    evidence_numbers: set[float] | None = None,
) -> VerificationResult:
    numbers = (
        evidence_numbers if evidence_numbers is not None else collect_evidence_numbers(evidence)
    )

    unresolved = resolver.unresolved(claim.citations)

    derived_values: set[float] = set()
    if claim.derived is not None:
        derived_values.add(round(claim.derived.result, 4))
        unresolved.extend(resolver.unresolved(claim.derived.inputs))

    findings: list[NumericFinding] = []
    unsupported: list[str] = []

    for value, as_written in extract_numbers(claim.text):
        if YEAR_RE.fullmatch(as_written):
            findings.append(NumericFinding(value=value, as_written=as_written,
                                           supported=True, how="year"))
            continue
        if _matches_evidence(value, numbers):
            findings.append(NumericFinding(value=value, as_written=as_written,
                                           supported=True, how="evidence"))
            continue
        if value in derived_values:
            findings.append(NumericFinding(value=value, as_written=as_written,
                                           supported=True, how="derived"))
            continue
        if value <= TRIVIAL_MAX and "." not in as_written and "," not in as_written:
            # Small bare integers are counts and enumerations, not published figures.
            findings.append(NumericFinding(value=value, as_written=as_written,
                                           supported=True, how="trivial"))
            continue
        findings.append(NumericFinding(value=value, as_written=as_written,
                                       supported=False, how="unsupported"))
        unsupported.append(as_written)

    return VerificationResult(
        ok=not unresolved and not unsupported,
        unresolved_citations=tuple(unresolved),
        unsupported_numbers=tuple(unsupported),
        findings=tuple(findings),
    )


def verify_claims(
    claims: Iterable[Claim], evidence: Any, resolver: CitationResolver | None = None
) -> tuple[list[Claim], list[tuple[Claim, VerificationResult]]]:
    """Split claims into those that verify and those that must be dropped."""
    resolver = resolver or CitationResolver()
    numbers = collect_evidence_numbers(evidence)

    kept: list[Claim] = []
    dropped: list[tuple[Claim, VerificationResult]] = []
    for claim in claims:
        result = verify_claim(claim, evidence, resolver, numbers)
        if result.ok:
            kept.append(claim)
        else:
            dropped.append((claim, result))
            log.warning(
                "verify.claim_dropped",
                text=claim.text[:160],
                unresolved=list(result.unresolved_citations),
                unsupported=list(result.unsupported_numbers),
            )
    return kept, dropped
