"""Conflict surfacing and the Part A / Part B separation guard."""

from __future__ import annotations

import pytest

from app.domain.citation import DocumentCitation, FilingCitation, PlantCitation
from app.domain.enums import Domain
from app.domain.note import Claim, Conflict, Section
from app.services.conflict_detector import capacity_mentions, detect_capacity_conflicts
from app.services.datasets import load_plants
from app.services.domain_guard import enforce
from ingestion.part_a_docs import load_documents


@pytest.fixture(scope="module")
def corpus():
    return load_documents(), load_plants()


@pytest.fixture(scope="module")
def conflicts(corpus):
    return detect_capacity_conflicts(*corpus)


# --- the conflict that matters ---------------------------------------------


def test_cilegon_conflict_is_surfaced_with_both_positions(conflicts):
    conflict = next(c for c in conflicts if "Cilegon Cracker 1" in c.subject)
    values = {p.value for p in conflict.positions}
    assert values == {"1,200 kta", "1,050 kta"}

    company = next(p for p in conflict.positions if p.value == "1,200 kta")
    monitor = next(p for p in conflict.positions if p.value == "1,050 kta")

    assert "Nusantara Olefins" in company.source_label
    assert monitor.source_label == "ASEAN Petrochemical Monitor"

    tokens = {t for p in conflict.positions for t in (c.token for c in p.citations)}
    assert "plant:PL-001" in tokens
    assert "doc:001_nusantara_cilegon_expansion_2026-03-11.md" in tokens
    assert "doc:002_asean_monitor_capacity_note_2026-06-02.md" in tokens


def test_conflict_carries_the_monitors_stated_reason(conflicts):
    """Presenting both numbers is half the job; why they differ is the other half."""
    conflict = next(c for c in conflicts if "Cilegon Cracker 1" in c.subject)
    monitor = next(p for p in conflict.positions if p.value == "1,050 kta")
    assert monitor.stated_reason is not None
    assert "confirmed nameplate" in monitor.stated_reason


def test_conflict_cannot_carry_a_resolution():
    """The schema makes silently picking a side unrepresentable."""
    assert "resolution" not in Conflict.model_fields
    with pytest.raises(ValueError):
        Conflict(subject="x", positions=[], resolution="1,200 kta")  # type: ignore[call-arg]


def test_conflict_requires_at_least_two_positions():
    with pytest.raises(ValueError):
        Conflict(subject="x", positions=[])


# --- no spurious conflicts --------------------------------------------------


def test_only_the_real_disagreement_is_reported(conflicts):
    """Every other linked capacity mention in the corpus agrees with the register.

    A detector that manufactures conflicts trains the reader to skip the section.
    """
    assert len(conflicts) == 1, [c.subject for c in conflicts]


def test_agreeing_sources_are_not_conflicts(corpus):
    """Doc 011's 250 kta HDPE line matches PL-025 exactly."""
    docs, plants = corpus
    doc011 = next(d for d in docs if d.doc_number == 11)
    mentions = capacity_mentions(doc011, plants)
    linked = [m for m in mentions if m.plant_id == "PL-025"]
    assert linked and linked[0].value == 250.0


def test_two_capacities_in_one_sentence_attach_to_different_units(corpus):
    """Regression: doc 009 says "the ethylene design capacity of 750 kta is
    unchanged, as is the 300 kta HDPE line".

    Taking the first product word in the sentence put both figures on the ethylene
    unit and produced a false 300-vs-750 conflict.
    """
    docs, plants = corpus
    doc009 = next(d for d in docs if d.doc_number == 9)
    linked = {m.value: m.plant_id for m in capacity_mentions(doc009, plants) if m.plant_id}
    assert linked[750.0] == "PL-006"   # Vung Tau cracker, ethylene
    assert linked[300.0] == "PL-026"   # the associated HDPE line


def test_cracker_capacity_is_ethylene_not_the_nearby_polymer_line(corpus):
    """Regression: doc 016 says "a 1,400 kta naphtha cracker ... together with HDPE
    and polypropylene lines". The 1,400 belongs to the cracker."""
    docs, plants = corpus
    doc016 = next(d for d in docs if d.doc_number == 16)
    linked = {m.value: m.plant_id for m in capacity_mentions(doc016, plants) if m.plant_id}
    assert linked[1400.0] == "PL-035"
    assert not any(c.subject.startswith("Bohai HDPE 1") for c in
                   detect_capacity_conflicts(docs, plants))


def test_unlinkable_mentions_are_left_unlinked(corpus):
    """Conservative by design: a wrong link is worse than no link."""
    docs, plants = corpus
    unlinked = [
        m for d in docs for m in capacity_mentions(d, plants) if m.plant_id is None
    ]
    assert unlinked, "expected some figures to be genuinely ambiguous"


# --- domain guard -----------------------------------------------------------


def _section(*claims: Claim) -> Section:
    return Section(id="s1", kind="supply_base", title="Supply", claims=list(claims))


def test_part_a_claim_passes_in_part_a():
    section = _section(
        Claim(text="Cilegon Cracker 1 is listed at 1,200 kta.",
              citations=[PlantCitation(plant_id="PL-001")])
    )
    cleaned, violations = enforce([section], Domain.PART_A)
    assert violations == []
    assert len(cleaned[0].claims) == 1


def test_filing_citation_is_dropped_from_a_part_a_note():
    section = _section(
        Claim(text="Revenue was 152,441 million.",
              citations=[FilingCitation(company="PETRONAS", filename="x.pdf", page=2)])
    )
    cleaned, violations = enforce([section], Domain.PART_A)
    assert cleaned[0].claims == []
    assert len(violations) == 1
    assert violations[0].offending_citations == ("filing:PETRONAS|x.pdf|p.2",)
    assert "never combined" in cleaned[0].coverage_note


def test_part_a_citation_is_dropped_from_a_part_b_note():
    section = _section(
        Claim(text="The register lists 52 plants.",
              citations=[PlantCitation(plant_id="PL-001")])
    )
    cleaned, violations = enforce([section], Domain.PART_B)
    assert cleaned[0].claims == []
    assert violations[0].offending_citations == ("plant:PL-001",)


def test_a_claim_mixing_both_domains_is_dropped():
    """The case the README forbids most directly."""
    section = _section(
        Claim(
            text="Nusantara Olefins operates 1,200 kta against Reliance's larger base.",
            citations=[
                PlantCitation(plant_id="PL-001"),
                FilingCitation(company="RIL", filename="y.pdf", page=1),
            ],
        )
    )
    for domain in (Domain.PART_A, Domain.PART_B):
        cleaned, violations = enforce([section], domain)
        assert cleaned[0].claims == []
        assert violations


def test_guard_keeps_clean_claims_alongside_dropped_ones():
    section = _section(
        Claim(text="Cilegon Cracker 1 is listed at 1,200 kta.",
              citations=[PlantCitation(plant_id="PL-001")]),
        Claim(text="Revenue was 152,441 million.",
              citations=[FilingCitation(company="PETRONAS", filename="x.pdf", page=2)]),
        Claim(text="The Monitor carries a lower figure.",
              citations=[DocumentCitation(filename="002_asean_monitor_capacity_note_2026-06-02.md")]),
    )
    cleaned, violations = enforce([section], Domain.PART_A)
    assert len(cleaned[0].claims) == 2
    assert len(violations) == 1
