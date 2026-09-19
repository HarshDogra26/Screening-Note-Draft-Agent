"""The register and the price series use different region vocabularies.

A plant is in "Southeast Asia"; a price is assessed at "Southeast Asia CFR". A live
run showed the agent supplying the register's vocabulary to the price tool and, having
been rejected, making the identical mistake on the next call — so the tool both
accepts an unambiguous name and explains itself when it cannot.
"""

from __future__ import annotations

import json

import pytest
from fastmcp import Client

from app.domain.enums import WarningCode
from app.graph.tools import ToolSession, _allowed_values
from app.services import price_query as prq
from mcp_servers.screening_server import mcp


async def call(name: str, **kwargs):
    async with Client(mcp) as client:
        result = await client.call_tool(name, kwargs)
    if result.structured_content and "result" in result.structured_content:
        return result.structured_content["result"]
    return result.structured_content or json.loads(result.content[0].text)


# --- resolving a region name ------------------------------------------------


@pytest.mark.parametrize("product", ["PP", "HDPE", "Propylene"])
def test_register_region_resolves_when_unambiguous(product):
    """"Southeast Asia" matches exactly one assessment location for these products."""
    result = prq.query_series(product, "Southeast Asia")
    assert not isinstance(result, dict), result
    assert result.region == "Southeast Asia CFR"
    assert result.points


def test_resolution_is_reported_not_silent():
    """A note must never attribute a figure to a location that was not assessed."""
    result = prq.query_series("PP", "Southeast Asia")
    warning = next(
        w for w in result.comparability_warnings if w.code is WarningCode.REGION_MISMATCH
    )
    assert "was read as" in warning.detail
    assert "Southeast Asia CFR" in warning.detail


def test_exact_names_produce_no_resolution_warning():
    result = prq.query_series("PP", "Southeast Asia CFR")
    assert not any(
        "was read as" in w.detail for w in result.comparability_warnings
    )


def test_ambiguous_name_is_refused_rather_than_guessed():
    """Ethylene is assessed in two locations; 'Asia' could mean either."""
    result = prq.query_series("Ethylene", "Asia")
    assert result["kind"] == "error"
    assert set(result["available_regions"]) == {"Southeast Asia CFR", "Northeast Asia CFR"}


def test_unambiguous_asia_resolves_for_naphtha():
    """Naphtha has exactly one location, so 'Asia' is not ambiguous for it."""
    result = prq.query_series("Naphtha", "Asia")
    assert not isinstance(result, dict)
    assert result.region == "Asia CFR"


def test_unknown_region_explains_the_two_vocabularies():
    result = prq.query_series("PP", "Europe")
    assert result["kind"] == "error"
    assert "not an assessment location" in result["error"]
    assert "plant register" in result["hint"]
    assert "Southeast Asia CFR" in result["available_regions"]


def test_unknown_product_lists_the_assessed_ones():
    result = prq.query_series("Benzene", "Southeast Asia CFR")
    assert result["kind"] == "error"
    assert "Ethylene" in result["available_products"]


def test_wrong_basis_lists_the_available_ones():
    """Propylene 2026 is Contract only; asking for Spot must say what exists."""
    result = prq.query_series(
        "Propylene", "Southeast Asia CFR", "2026-01", "2026-08", basis="Spot"
    )
    assert result["kind"] == "error"
    assert "Contract" in result["available_bases"]


# --- the exact calls from the live trace ------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("product", ["PP", "HDPE"])
async def test_the_calls_that_failed_on_a_live_run_now_succeed(product):
    out = await call(
        "query_price_series",
        product=product,
        region="Southeast Asia",
        month_from="2024-01",
        month_to="2026-08",
    )
    assert out["kind"] == "price_series"
    assert out["region"] == "Southeast Asia CFR"
    assert out["points"]


# --- allowed values reach the agent -----------------------------------------


@pytest.mark.asyncio
async def test_signatures_state_the_allowed_region_values():
    """Rendering only "region: string (optional)" is what let the wrong vocabulary
    through in the first place."""
    async with ToolSession(mcp, domain="part_a") as session:
        signatures = await session.describe_tools()
    assert "'Southeast Asia CFR'" in signatures
    assert "'Northeast Asia CFR'" in signatures
    assert "basis must be one of: 'Spot', 'Contract'" in signatures


def test_enum_hint_is_taken_from_a_real_list():
    spec = {"description": "'Spot' or 'Contract'."}
    assert _allowed_values(spec) == "'Spot', 'Contract'"


def test_enum_hint_is_not_invented_from_an_example():
    """"Second product for compute='spread', e.g. 'Ethylene'" is an example, not an
    enumeration. Rendering it as one would have the agent believe a fabricated
    constraint."""
    spec = {"description": "Second product for compute='spread', e.g. 'Ethylene'."}
    assert _allowed_values(spec) is None


def test_enum_hint_prefers_a_real_json_schema_enum():
    spec = {"enum": ["a", "b"], "description": "'x' or 'y'."}
    assert _allowed_values(spec) == "'a', 'b'"


def test_single_quoted_value_is_not_an_enum():
    assert _allowed_values({"description": "Exact company name, like 'Nusantara'."}) is None
