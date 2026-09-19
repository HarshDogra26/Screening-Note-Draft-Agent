"""The trap suite.

One test per trap in ARCHITECTURE.md section 2.1. These run against the real CSVs
with no LLM, no network and no vector store. If any of these fail, the agent above
them cannot be correct regardless of how good the prompting is.
"""

from __future__ import annotations

import pytest

from app.domain.enums import ComparabilityVerdict, WarningCode
from app.services import plant_query as pq
from app.services import price_query as prq
from app.services.datasets import load_plants, load_prices

# --- fixtures --------------------------------------------------------------


@pytest.fixture(scope="module")
def plants():
    return load_plants()


@pytest.fixture(scope="module")
def prices():
    return load_prices()


# --- dataset shape (the ingestion validation gate) -------------------------


def test_dataset_shape(plants, prices):
    assert len(plants) == 52
    assert len(prices) == 190
    assert {p.status for p in plants} == {"Operating", "Under construction", "Idled"}
    assert {r.unit for r in prices} == {"USD/tonne"}


# --- trap 1: basis break ---------------------------------------------------


def test_propylene_basis_break_refuses_change(prices):
    """Propylene SE Asia switches Spot -> Contract at 2026-01.

    Doc 025: "The resulting step in the published series reflects the change of basis
    and should not be read as a market movement."
    """
    result = prq.query_series(
        "Propylene", "Southeast Asia CFR", "2024-01", "2026-08", compute="change", rows=prices
    )
    assert result.verdict is ComparabilityVerdict.NOT_COMPARABLE
    assert result.change is None
    assert result.change_pct is None
    assert "basis break" in result.change_refused_reason

    codes = {w.code for w in result.comparability_warnings}
    assert WarningCode.BASIS_CHANGE in codes

    basis_warning = next(
        w for w in result.comparability_warnings if w.code is WarningCode.BASIS_CHANGE
    )
    assert basis_warning.corroborating_document == (
        "025_asean_monitor_price_commentary_2026-06-18.md"
    )

    # Per-segment statistics are offered in place of the refused delta.
    assert [s.basis for s in result.basis_segments] == ["Spot", "Contract"]
    assert result.basis_segments[0].month_to == "2025-12"
    assert result.basis_segments[1].month_from == "2026-01"


def test_single_basis_segment_is_comparable(prices):
    """The refusal must be specific to the break, not blanket caution."""
    result = prq.query_series(
        "Ethylene", "Southeast Asia CFR", "2026-01", "2026-08", compute="change", rows=prices
    )
    assert result.verdict is ComparabilityVerdict.COMPARABLE
    assert result.change is not None


# --- trap 2: absent months -------------------------------------------------


def test_hdpe_missing_months_are_reported_not_interpolated(prices):
    """HDPE SE Asia has no rows at all for 2026-01 and 2026-02."""
    result = prq.query_series("HDPE", "Southeast Asia CFR", "2025-12", "2026-03", rows=prices)
    assert result.continuity.missing_months == ("2026-01", "2026-02")
    assert result.continuity.months_requested == 4
    assert result.continuity.months_present == 2
    assert {p.month for p in result.points} == {"2025-12", "2026-03"}
    assert result.verdict is ComparabilityVerdict.COMPARABLE_WITH_CAVEAT

    gap = next(w for w in result.comparability_warnings if w.code is WarningCode.COVERAGE_GAP)
    assert "not interpolated" in gap.detail


# --- trap 3: blank values (distinct from trap 2) ---------------------------


def test_pp_blank_prices_are_unconfirmed_not_missing(prices):
    """PP SE Asia 2026-07/08 rows EXIST but carry no price.

    This is a different fact from HDPE's absent months and must not be collapsed
    into the same bucket.
    """
    result = prq.query_series("PP", "Southeast Asia CFR", "2026-05", "2026-08", rows=prices)
    assert result.continuity.unconfirmed_months == ("2026-07", "2026-08")
    assert result.continuity.missing_months == ()  # rows are present
    assert result.continuity.months_present == 4

    withheld = [p for p in result.points if p.price is None]
    assert {p.month for p in withheld} == {"2026-07", "2026-08"}
    assert all(p.source_type == "Not publicly confirmed" for p in withheld)

    # A range containing withheld values must not read as cleanly comparable, even
    # though every month has a row.
    assert result.verdict is ComparabilityVerdict.COMPARABLE_WITH_CAVEAT
    warning = next(
        w
        for w in result.comparability_warnings
        if w.code is WarningCode.ENDPOINT_NOT_PUBLICLY_CONFIRMED
    )
    assert "2 of 4 months" in warning.detail


def test_change_refused_when_endpoint_unconfirmed(prices):
    """A blank endpoint must not silently fall back to the nearest confirmed month."""
    result = prq.query_series(
        "PP", "Southeast Asia CFR", "2026-01", "2026-08", compute="change", rows=prices
    )
    assert result.change is None
    assert "not publicly confirmed" in result.change_refused_reason
    assert "last confirmed month" in result.change_refused_reason


# --- trap 4: blank capacity ------------------------------------------------


def test_blank_capacity_excluded_and_reported(plants):
    """PL-008 has no publicly confirmed capacity. It is not zero."""
    pl008 = next(p for p in plants if p.plant_id == "PL-008")
    assert pl008.capacity_kta is None
    assert pl008.source_type == "Not publicly confirmed"

    result = pq.sum_capacity(
        region="Southeast Asia", product="Ethylene", status=["Operating"], rows=plants
    )
    withheld = {w["plant_id"] for w in result["not_publicly_confirmed"]}
    assert "PL-008" in withheld
    assert result["n_included"] == result["n_matched"] - len(withheld)
    assert "not counted as zero" in result["caveat"]

    # The total must equal the sum of the units it says it covers, and nothing more.
    included = pq.query_plants(
        region="Southeast Asia", product="Ethylene", status=["Operating"], rows=plants
    )
    expected = sum(p.capacity_kta for p in included if p.capacity_kta is not None)
    assert result["sum_capacity_kta"] == expected


# --- trap 8: status leakage ------------------------------------------------


def test_aggregate_without_status_is_refused(plants):
    result = pq.sum_capacity(region="Southeast Asia", product="Ethylene", rows=plants)
    assert result["kind"] == "error"
    assert "explicit status filter" in result["error"]


def test_under_construction_excluded_from_operating_total(plants):
    result = pq.sum_capacity(
        region="Southeast Asia", product="Ethylene", status=["Operating"], rows=plants
    )
    excluded = {e["plant_id"] for e in result["excluded_by_status"]}
    assert "PL-006" in excluded  # Vung Tau, Under construction, 750 kta
    assert "PL-006" not in set(result["citations"])


# --- trap 9: region mismatch -----------------------------------------------


def test_naphtha_ethylene_spread_flags_region_mismatch(prices):
    """Naphtha is assessed 'Asia CFR'; ethylene 'Southeast Asia CFR'."""
    result = prq.query_spread(
        "Naphtha", "Ethylene", region_b="Southeast Asia CFR", month_from="2026-01",
        month_to="2026-06", rows=prices,
    )
    assert result["region_mismatch"] is True
    codes = {w["code"] for w in result["comparability_warnings"]}
    assert WarningCode.REGION_MISMATCH.value in codes
    assert result["subtrahend"]["region"] == "Asia CFR"
    assert result["minuend"]["region"] == "Southeast Asia CFR"
    assert len(result["months"]) == 6


# --- ambiguity handling ----------------------------------------------------


def test_ambiguous_region_is_refused_with_options(prices):
    """Ethylene is assessed in two locations; the engine must not pick one."""
    result = prq.query_series("Ethylene", rows=prices)
    assert result["kind"] == "error"
    assert set(result["available_regions"]) == {"Southeast Asia CFR", "Northeast Asia CFR"}


def test_naphtha_single_region_resolves(prices):
    """Where there is exactly one assessment location, defaulting is safe."""
    result = prq.query_series("Naphtha", rows=prices)
    assert result.region == "Asia CFR"


# --- determinism -----------------------------------------------------------


def test_queries_are_order_stable(plants, prices):
    a = pq.list_plants(region="Southeast Asia", rows=plants)
    b = pq.list_plants(region="Southeast Asia", rows=plants)
    assert a["citations"] == b["citations"] == sorted(a["citations"])

    s1 = prq.query_series("Ethylene", "Southeast Asia CFR", rows=prices)
    s2 = prq.query_series("Ethylene", "Southeast Asia CFR", rows=prices)
    assert s1.model_dump() == s2.model_dump()
