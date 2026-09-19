"""Grounding verification: citation resolution and numeric provenance.

The fabrication tests are the point of this file. A model that cites a real file for
an invented number must fail here.
"""

from __future__ import annotations

import pytest

from app.domain.citation import DocumentCitation, FilingCitation, PlantCitation, PriceCitation
from app.domain.note import Claim, DerivedCalc
from app.services.grounding import (
    CitationResolver,
    collect_evidence_numbers,
    extract_numbers,
    verify_claim,
    verify_claims,
)


@pytest.fixture(scope="module")
def resolver():
    return CitationResolver()


# --- citation resolution ----------------------------------------------------


def test_real_citations_resolve(resolver):
    assert resolver.resolve(PlantCitation(plant_id="PL-001"))
    assert resolver.resolve(
        PriceCitation(product="Ethylene", region="Southeast Asia CFR",
                      month="2026-04", basis="Spot")
    )
    assert resolver.resolve(
        DocumentCitation(filename="002_asean_monitor_capacity_note_2026-06-02.md")
    )


def test_invented_citations_do_not_resolve(resolver):
    assert not resolver.resolve(PlantCitation(plant_id="PL-999"))
    assert not resolver.resolve(DocumentCitation(filename="099_invented_document.md"))


def test_price_citation_must_match_the_exact_basis(resolver):
    """Propylene 2026-03 exists on Contract, not on Spot.

    Citing the wrong basis is how a note ends up comparing across the methodology
    break while appearing properly sourced.
    """
    assert resolver.resolve(
        PriceCitation(product="Propylene", region="Southeast Asia CFR",
                      month="2026-03", basis="Contract")
    )
    assert not resolver.resolve(
        PriceCitation(product="Propylene", region="Southeast Asia CFR",
                      month="2026-03", basis="Spot")
    )


def test_price_citation_for_a_month_with_no_row_does_not_resolve(resolver):
    """HDPE has no row at all for 2026-01."""
    assert not resolver.resolve(
        PriceCitation(product="HDPE", region="Southeast Asia CFR",
                      month="2026-01", basis="Spot")
    )


def test_filing_page_out_of_range_does_not_resolve(resolver):
    assert resolver.resolve(
        FilingCitation(company="GC", filename="pttgc-one-report2025-en.pdf", page=140)
    )
    assert not resolver.resolve(
        FilingCitation(company="GC", filename="pttgc-one-report2025-en.pdf", page=9999)
    )


# --- number extraction ------------------------------------------------------


def test_extract_numbers_handles_thousands_and_decimals():
    values = dict((raw, val) for val, raw in extract_numbers(
        "Capacity is 1,200 kta, the mean was 818.38 and the change was 45."
    ))
    assert values["1,200"] == 1200.0
    assert values["818.38"] == 818.38
    assert values["45"] == 45.0


def test_collect_evidence_numbers_reads_nested_payloads_and_prose():
    evidence = {
        "rows": [{"plant_id": "PL-001", "capacity_kta": 1200}],
        "text": "the company describes the unit as having an ethylene capacity of 1,200 kta",
        "nested": {"sum_capacity_kta": 5370.0},
    }
    numbers = collect_evidence_numbers(evidence)
    assert 1200.0 in numbers
    assert 5370.0 in numbers


# --- the fabrication check --------------------------------------------------


def test_claim_grounded_in_evidence_passes(resolver):
    evidence = {"sum_capacity_kta": 5370.0, "n_included": 7}
    claim = Claim(
        text="Operating ethylene capacity in Southeast Asia totals 5,370 kta.",
        citations=[PlantCitation(plant_id="PL-001")],
    )
    assert verify_claim(claim, evidence, resolver).ok


def test_fabricated_number_with_a_real_citation_is_caught(resolver):
    """The load-bearing test.

    The citation is genuine and resolves. The number was never in the evidence. A
    check that only validated citations would pass this straight through.
    """
    evidence = {"sum_capacity_kta": 5370.0}
    claim = Claim(
        text="Operating ethylene capacity in Southeast Asia totals 6,800 kta.",
        citations=[PlantCitation(plant_id="PL-001")],
    )
    result = verify_claim(claim, evidence, resolver)
    assert not result.ok
    assert "6,800" in result.unsupported_numbers
    assert "not found in evidence" in result.reason


def test_rounding_down_is_allowed_but_inventing_precision_is_not(resolver):
    evidence = {"mean": 818.38}

    rounded = Claim(text="The contract mean was 818 USD/tonne.",
                    citations=[PlantCitation(plant_id="PL-001")])
    assert verify_claim(rounded, evidence, resolver).ok

    # Evidence says 818; a note may not report 818.38.
    invented = Claim(text="The contract mean was 818.38 USD/tonne.",
                     citations=[PlantCitation(plant_id="PL-001")])
    assert not verify_claim(invented, {"mean": 818}, resolver).ok


def test_years_do_not_need_provenance(resolver):
    claim = Claim(
        text="The unit is expected to start up in 2028.",
        citations=[PlantCitation(plant_id="PL-006")],
    )
    assert verify_claim(claim, {"startup_year": 2028}, resolver).ok


def test_small_counts_do_not_need_provenance(resolver):
    claim = Claim(
        text="Two units are under construction.",
        citations=[PlantCitation(plant_id="PL-006")],
    )
    assert verify_claim(claim, {}, resolver).ok


def test_derived_number_needs_a_formula_and_cited_inputs(resolver):
    inputs = [
        PriceCitation(product="Naphtha", region="Asia CFR", month="2026-04", basis="Spot"),
        PriceCitation(product="Ethylene", region="Southeast Asia CFR",
                      month="2026-04", basis="Spot"),
    ]
    claim = Claim(
        text="The April 2026 ethylene-naphtha spread was 429 USD/tonne.",
        citations=inputs,
        derived=DerivedCalc(formula="Ethylene - Naphtha", inputs=inputs, result=429.0,
                            unit="USD/tonne"),
    )
    assert verify_claim(claim, {}, resolver).ok


def test_derived_claim_with_an_unresolvable_input_fails(resolver):
    bad = [PriceCitation(product="HDPE", region="Southeast Asia CFR",
                         month="2026-01", basis="Spot")]
    claim = Claim(
        text="The spread was 429 USD/tonne.",
        citations=[PlantCitation(plant_id="PL-001")],
        derived=DerivedCalc(formula="a - b", inputs=bad, result=429.0),
    )
    result = verify_claim(claim, {}, resolver)
    assert not result.ok
    assert result.unresolved_citations


def test_unresolvable_citation_fails_even_when_numbers_check_out(resolver):
    claim = Claim(
        text="Capacity is 1,200 kta.",
        citations=[PlantCitation(plant_id="PL-999")],
    )
    result = verify_claim(claim, {"capacity_kta": 1200}, resolver)
    assert not result.ok
    assert "plant:PL-999" in result.unresolved_citations


# --- batch behaviour --------------------------------------------------------


def test_verify_claims_drops_only_the_bad_ones(resolver):
    evidence = {"sum_capacity_kta": 5370.0, "capacity_kta": 1200}
    good = Claim(text="Cilegon Cracker 1 is listed at 1,200 kta.",
                 citations=[PlantCitation(plant_id="PL-001")])
    bad = Claim(text="Regional capacity reached 9,999 kta.",
                citations=[PlantCitation(plant_id="PL-001")])

    kept, dropped = verify_claims([good, bad], evidence, resolver)
    assert [c.text for c in kept] == [good.text]
    assert len(dropped) == 1
    assert "9,999" in dropped[0][1].unsupported_numbers


def test_claim_cannot_be_constructed_without_a_citation():
    """Claim-level grounding is enforced by the type, not by a prompt."""
    with pytest.raises(ValueError):
        Claim(text="Capacity is 1,200 kta.", citations=[])
