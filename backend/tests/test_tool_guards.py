"""Guards added after the first live run.

Two failure modes, both found by running the agent for real rather than by reasoning
about it:

1. An unrecognised filter value silently matched nothing, and the agent read the empty
   result as proof the corpus lacked the figure. It reported a confident gap for a
   number printed on page 2 of the filing it had just searched.
2. A Part A brief called a Part B tool. The assembly guard only inspects citations, so
   a section that spent its whole budget in the wrong corpus came back empty with
   nothing cross-domain left to catch.
"""

from __future__ import annotations

import json

import pytest
from fastmcp import Client

from app.graph.tools import DOMAIN_TOOLS, ToolSession
from mcp_servers.screening_server import mcp


async def call(name: str, **kwargs):
    async with Client(mcp) as client:
        result = await client.call_tool(name, kwargs)
    if result.structured_content and "result" in result.structured_content:
        return result.structured_content["result"]
    return result.structured_content or json.loads(result.content[0].text)


# --- filter values that cannot match must fail loudly -----------------------


@pytest.mark.asyncio
async def test_filename_passed_as_doc_type_is_an_error_not_an_empty_result():
    """The exact call that produced the false gap on a live run."""
    out = await call(
        "search_part_b_filings",
        query="revenue",
        company="PETRONAS",
        doc_type="PETRONAS Interim Financial Report 1H 2026 28.8.2026.pdf",
    )
    assert out["kind"] == "error"
    assert "interim_report" in out["available_doc_types"]
    assert "filename" in out["hint"]


@pytest.mark.asyncio
async def test_filename_filter_works_when_used_correctly():
    out = await call(
        "search_part_b_filings",
        query="revenue",
        filename="PETRONAS Interim Financial Report 1H 2026 28.8.2026.pdf",
        k=3,
    )
    assert out["kind"] == "part_b_filings"
    assert out["n"] > 0
    assert all(r["filename"].startswith("PETRONAS Interim") for r in out["results"])


@pytest.mark.asyncio
async def test_unknown_filename_lists_the_real_ones():
    out = await call("search_part_b_filings", query="revenue", filename="nope.pdf")
    assert out["kind"] == "error"
    assert any("PETRONAS" in f for f in out["available_filenames"])


@pytest.mark.asyncio
async def test_unknown_company_filter_on_documents_is_an_error():
    out = await call("search_part_a_documents", query="capacity", company="Acme Chemicals")
    assert out["kind"] == "error"
    assert "Nusantara Olefins" in out["available_companies"]


@pytest.mark.asyncio
async def test_unknown_source_type_is_an_error():
    out = await call("search_part_a_documents", query="capacity", source_type="Blog post")
    assert out["kind"] == "error"
    assert "Company press release" in out["available_source_types"]


# --- an empty result must explain itself ------------------------------------


@pytest.mark.asyncio
async def test_empty_filings_result_warns_against_reading_it_as_absence():
    out = await call(
        "search_part_b_filings", query="zzzzz nonexistent term", company="GC",
        doc_type="media_release",
    )
    assert out["n"] == 0
    assert "does NOT establish" in out["empty_result_note"]
    assert out["filters_applied"] == {"company": "GC", "doc_type": "media_release"}


@pytest.mark.asyncio
async def test_empty_document_result_warns_too():
    out = await call(
        "search_part_a_documents", query="capacity",
        company="Nusantara Olefins", date_from="2030-01-01",
    )
    assert out["n"] == 0
    assert "does NOT establish" in out["empty_result_note"]


# --- domain-scoped tool access ----------------------------------------------


@pytest.mark.asyncio
async def test_part_a_session_cannot_call_the_filings_tool():
    async with ToolSession(mcp, domain="part_a") as session:
        out = await session.call("search_part_b_filings", {"query": "revenue"})
    assert out["kind"] == "error"
    assert "other dataset" in out["error"]
    assert "search_part_b_filings" not in out["available"]


@pytest.mark.asyncio
async def test_part_b_session_cannot_call_the_register():
    async with ToolSession(mcp, domain="part_b") as session:
        out = await session.call("query_plant_register", {"plant_ids": ["PL-001"]})
    assert out["kind"] == "error"
    assert "other dataset" in out["error"]


@pytest.mark.asyncio
async def test_describe_corpus_is_available_to_both_domains():
    for domain in ("part_a", "part_b"):
        async with ToolSession(mcp, domain=domain) as session:
            out = await session.call("describe_corpus", {"section": "all"})
        assert out["kind"] == "corpus_description"


@pytest.mark.asyncio
async def test_signatures_hide_the_other_domains_tools():
    """Hidden rather than merely blocked, so no call is wasted discovering it."""
    async with ToolSession(mcp, domain="part_a") as session:
        signatures = await session.describe_tools()
    assert "query_plant_register" in signatures
    assert "search_part_b_filings" not in signatures


@pytest.mark.asyncio
async def test_signatures_name_real_arguments():
    """The gather prompt showed tools in prose only, and the model invented argument
    names — 11 rejected calls on one section before the budget ran out."""
    async with ToolSession(mcp, domain="part_a") as session:
        signatures = await session.describe_tools()
    assert "product:" in signatures
    assert "month_from:" in signatures
    assert "required" in signatures and "optional" in signatures


@pytest.mark.asyncio
async def test_unscoped_session_allows_everything():
    """A session with no domain is unrestricted, as health checks need."""
    async with ToolSession(mcp) as session:
        out = await session.call("describe_corpus", {"section": "register"})
    assert out["kind"] == "corpus_description"


def test_domain_tool_sets_are_disjoint_apart_from_discovery():
    shared = DOMAIN_TOOLS["part_a"] & DOMAIN_TOOLS["part_b"]
    assert shared == {"describe_corpus"}


# --- tool errors are logged with their message ------------------------------


@pytest.mark.asyncio
async def test_tool_errors_carry_their_message_into_the_call_record():
    """The first live run logged only kind=error, so the cause had to be
    reconstructed from the MCP server's own logs."""
    async with ToolSession(mcp, domain="part_b") as session:
        await session.call("search_part_b_filings", {"query": "revenue", "doc_type": "bogus"})
        record = session.calls[-1]
    assert record["kind"] == "error"
    assert "bogus" in record["error"]


@pytest.mark.asyncio
async def test_schema_rejections_are_logged_with_their_message_too():
    """A malformed call rejected by the tool schema, before the handler runs, must
    still reach the agent with a usable explanation rather than a bare 'error'."""
    async with ToolSession(mcp, domain="part_b") as session:
        await session.call("search_part_b_filings", {"query": "x"})  # below min_length
        record = session.calls[-1]
    assert record["kind"] == "error"
    assert "at least 2 characters" in record["error"]
