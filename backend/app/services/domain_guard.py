"""The Part A / Part B separation guard.
"""

from __future__ import annotations
from pydantic import BaseModel, ConfigDict
from ..domain.citation import DOMAIN_ALLOWED_KINDS, Citation
from ..domain.enums import Domain, SourceKind
from ..domain.note import Claim, Section
from ..logging import get_logger

log = get_logger(__name__)


class Violation(BaseModel):
    model_config = ConfigDict(frozen=True)

    where: str
    claim_text: str
    offending_citations: tuple[str, ...]
    domain: Domain


def allowed_kinds(domain: Domain) -> frozenset[SourceKind]:
    return DOMAIN_ALLOWED_KINDS[domain.value]


def offending(citations: list[Citation], domain: Domain) -> list[str]:
    permitted = allowed_kinds(domain)
    return [c.token for c in citations if c.kind not in permitted]


def check_claim(claim: Claim, domain: Domain, where: str) -> Violation | None:
    bad = offending(list(claim.citations), domain)
    if claim.derived is not None:
        bad.extend(offending(list(claim.derived.inputs), domain))
    if not bad:
        return None
    return Violation(
        where=where,
        claim_text=claim.text[:200],
        offending_citations=tuple(sorted(set(bad))),
        domain=domain,
    )


def enforce(sections: list[Section], domain: Domain) -> tuple[list[Section], list[Violation]]:
    """Drop every claim citing the other domain. Returns cleaned sections and what went.
    """
    violations: list[Violation] = []
    cleaned: list[Section] = []

    for section in sections:
        kept: list[Claim] = []
        for claim in section.claims:
            violation = check_claim(claim, domain, where=section.id)
            if violation is None:
                kept.append(claim)
            else:
                violations.append(violation)
                log.warning(
                    "guard.cross_domain_claim_dropped",
                    section=section.id,
                    domain=domain.value,
                    citations=list(violation.offending_citations),
                )

        note = section.coverage_note
        if len(kept) != len(section.claims):
            dropped = len(section.claims) - len(kept)
            extra = (
                f"{dropped} claim(s) were removed because they drew on the other dataset; "
                "Part A (synthetic) and Part B (real company filings) are never combined "
                "in one note."
            )
            note = f"{note} {extra}".strip() if note else extra

        cleaned.append(section.model_copy(update={"claims": kept, "coverage_note": note}))

    return cleaned, violations
