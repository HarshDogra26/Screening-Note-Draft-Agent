"""The MCP tools, exercised through the FastMCP client over an in-memory transport.

This goes through the real protocol -- schema validation, serialisation, the lot --
without needing a port or Azure credentials.
"""

from __future__ import annotations

import json

import pytest
from fastmcp import Client

from mcp_servers.screening_server import mcp


async def call(name: str, **kwargs):
    async with Client(mcp) as client:
        result = await client.call_tool(name, kwargs)
    if result.structured_content and "result" in result.structured_content:
        return result.structured_content["result"]
    return result.structured_content or json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_five_tools_are_exposed():
    async with Client(mcp) as client:
        tools = {t.name for t in await client.list_tools()}
    assert tools == {
        "describe_corpus",
        "query_plant_register",
        "query_price_series",
        "search_part_a_documents",
        "search_part_b_filings",
    }


@pytest.mark.asyncio
async def test_describe_corpus_gives_the_agent_real_filter_values():
    out = await call("describe_corpus", section="register")
    values = out["register"]["values"]
    assert "Southeast Asia" in values["region"]
    assert "PP" in values["product"]
    assert set(values["status"]) == {"Operating", "Under construction", "Idled"}
    assert any(
        p["plant_id"] == "PL-008" for p in out["register"]["capacity_not_publicly_confirmed"]
    )


@pytest.mark.asyncio
async def test_describe_corpus_exposes_gaps_and_the_basis_break():
    out = await call("describe_corpus", section="prices")
    inventory = out["price_series"]

    assert inventory["HDPE|Southeast Asia CFR|Spot"]["months_with_no_row"] == ["2026-01", "2026-02"]
    assert inventory["PP|Southeast Asia CFR|Spot"]["months_with_blank_price"] == [
        "2026-07",
        "2026-08",
    ]
    # The basis break is visible as two series before the agent queries anything.
    assert inventory["Propylene|Southeast Asia CFR|Spot"]["to"] == "2025-12"
    assert inventory["Propylene|Southeast Asia CFR|Contract"]["from"] == "2026-01"


@pytest.mark.asyncio
async def test_price_tool_refuses_change_across_basis_break():
    out = await call(
        "query_price_series",
        product="Propylene",
        region="Southeast Asia CFR",
        month_from="2024-01",
        month_to="2026-08",
        compute="change",
    )
    assert out["verdict"] == "not_comparable"
    assert out["change"] is None
    assert "basis break" in out["change_refused_reason"]
    codes = {w["code"] for w in out["comparability_warnings"]}
    assert "basis_change" in codes
    assert len(out["basis_segments"]) == 2


@pytest.mark.asyncio
async def test_price_tool_distinguishes_missing_from_unconfirmed():
    hdpe = await call(
        "query_price_series", product="HDPE", region="Southeast Asia CFR",
        month_from="2025-12", month_to="2026-03",
    )
    assert hdpe["continuity"]["missing_months"] == ["2026-01", "2026-02"]
    assert hdpe["continuity"]["unconfirmed_months"] == []

    pp = await call(
        "query_price_series", product="PP", region="Southeast Asia CFR",
        month_from="2026-05", month_to="2026-08",
    )
    assert pp["continuity"]["missing_months"] == []
    assert pp["continuity"]["unconfirmed_months"] == ["2026-07", "2026-08"]


@pytest.mark.asyncio
async def test_spread_flags_region_mismatch():
    out = await call(
        "query_price_series", product="Naphtha", compute="spread",
        spread_against="Ethylene", spread_against_region="Southeast Asia CFR",
        month_from="2026-01", month_to="2026-06",
    )
    assert out["region_mismatch"] is True
    assert any(w["code"] == "region_mismatch" for w in out["comparability_warnings"])


@pytest.mark.asyncio
async def test_aggregate_refused_without_status():
    out = await call("query_plant_register", region="Southeast Asia", product="Ethylene",
                     aggregate=True)
    assert out["kind"] == "error"
    assert "explicit status filter" in out["error"]


@pytest.mark.asyncio
async def test_aggregate_accounts_for_withheld_capacity():
    out = await call("query_plant_register", region="Southeast Asia", product="Ethylene",
                     status=["Operating"], aggregate=True)
    assert out["sum_capacity_kta"] == 5370
    assert out["n_included"] == 7
    assert out["n_matched"] == 8
    assert [w["plant_id"] for w in out["not_publicly_confirmed"]] == ["PL-008"]
    assert "not counted as zero" in out["caveat"]


@pytest.mark.asyncio
async def test_document_search_returns_full_text_with_attribution():
    out = await call("search_part_a_documents", query="Cilegon cracker capacity", k=5)
    filenames = [r["filename"] for r in out["results"]]
    assert "002_asean_monitor_capacity_note_2026-06-02.md" in filenames

    monitor = next(r for r in out["results"] if r["filename"].startswith("002_"))
    assert monitor["attribution"] == "ASEAN Petrochemical Monitor"
    assert monitor["is_independent"] is True
    # Full text, not a snippet: the 1,050 figure must survive retrieval.
    assert "1,050 kta" in monitor["text"]


@pytest.mark.asyncio
async def test_document_search_surfaces_scope_limitations():
    out = await call("search_part_a_documents", query="price forecast outlook 2027", k=8)
    limits = [line for r in out["results"] for line in r["scope_limitations"]]
    assert any("does not publish price forecasts or outlooks" in line for line in limits)


@pytest.mark.asyncio
async def test_document_search_surfaces_supersession_both_ways():
    out = await call("search_part_a_documents", query="Vung Tau startup schedule", k=6)
    by_file = {r["filename"]: r for r in out["results"]}

    old = by_file["008_mekong_vungtau_schedule_2026-01-28.md"]
    assert old["superseded_on"][0]["was"]["startup_year"] == "2027"
    assert old["superseded_on"][0]["now"]["startup_year"] == "2028"
    # The superseded document keeps the facts that were not revised.
    assert any("750 kta" in f for f in old["superseded_on"][0]["still_current_here"])

    new = by_file["009_mekong_vungtau_revised_2026-07-09.md"]
    assert "2027" in new["revises"] and "2028" in new["revises"]


@pytest.mark.asyncio
async def test_filings_search_attaches_basis_of_preparation():
    out = await call("search_part_b_filings", query="revenue EBITDA", company="GC", k=3)
    assert out["n"] > 0
    for result in out["results"]:
        basis = result["basis_of_preparation"]
        assert basis["reporting_currency"] == "THB"
        assert basis["fiscal_year_end"] == "31 December"
        assert result["citation"].startswith("filing:GC|")


@pytest.mark.asyncio
async def test_pcg_is_reported_as_absent_not_approximated():
    out = await call("search_part_b_filings", query="chemicals segment", company="PCG")
    assert out["kind"] == "error"
    assert set(out["available"]) == {"GC", "PETRONAS", "RIL"}
    assert "no PCG filing" in out["note"]


@pytest.mark.asyncio
async def test_petronas_results_warn_about_entity_scope():
    out = await call("search_part_b_filings", query="group performance revenue",
                     company="PETRONAS", k=2)
    assert out["results"]
    for result in out["results"]:
        assert result["legal_entity"] == "Petroliam Nasional Berhad (PETRONAS Group)"
        assert any("not the entity named by its folder" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_cross_company_search_spells_out_incomparability():
    out = await call("search_part_b_filings", query="revenue for the period", k=12)
    block = out.get("cross_company_comparability")
    assert block is not None, "multi-company results must carry the comparability block"
    assert len(block["companies"]) > 1
    currencies = set(block["currencies"].values())
    assert len(currencies) > 1
    assert "Do not convert" in block["warning"]


@pytest.mark.asyncio
async def test_filing_text_is_never_truncated():
    """Regression: the tool used to cut page text at 4,000 characters.

    Part B pages average ~5,100 chars and reach 6,700 in the two big annual reports,
    so a citation could point at a page whose second half was never read. Chunks are
    returned whole.
    """
    out = await call("search_part_b_filings", query="profit for the period", k=8)
    _, _, chunks, _ = __import__(
        "app.services.retrieval", fromlist=["part_b_index"]
    ).part_b_index()
    by_id = {c.chunk_id: c for c in chunks}
    assert out["results"]
    for result in out["results"]:
        assert result["text"] == by_id[result["chunk_id"]].text


@pytest.mark.asyncio
async def test_retrieval_mode_is_reported():
    """The note must be able to tell that semantic recall was unavailable.

    With no Azure credentials the suite runs lexical-only, which is a supported mode --
    but silently returning weaker results would let a note claim absence of evidence
    it never had the means to find.
    """
    out = await call("search_part_b_filings", query="segment performance", k=4)
    assert out["retrieval_mode"] in {
        "hybrid", "lexical_only", "lexical_only_dense_failed",
    }
    if out["retrieval_mode"] != "hybrid":
        assert "inconclusive rather than as absence" in out["retrieval_note"]


@pytest.mark.asyncio
async def test_table_chunks_are_labelled():
    """The agent should know when it is looking at a table rather than narrative."""
    out = await call("search_part_b_filings", query="revenue EBITDA profit total", k=12)
    kinds = {r["content_kind"] for r in out["results"]}
    assert kinds <= {"prose", "table"}


@pytest.mark.asyncio
async def test_tools_are_deterministic_across_calls():
    a = await call("search_part_a_documents", query="ethylene price April 2026", k=5)
    b = await call("search_part_a_documents", query="ethylene price April 2026", k=5)
    assert [r["filename"] for r in a["results"]] == [r["filename"] for r in b["results"]]
    assert [r["score"] for r in a["results"]] == [r["score"] for r in b["results"]]
