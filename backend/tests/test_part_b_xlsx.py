"""Spreadsheet ingestion for the PTT Global Chemical interim statements.

These two workbooks share a filename and state their period nowhere except in their
cells, so identifying them correctly is the whole job. Getting it wrong would attach
Q1 figures to a half-year label, or the reverse.
"""

from __future__ import annotations

import pytest

from app.api.sources import resolve
from ingestion.chunking import ChunkKind, chunk_filing
from ingestion.part_b_filings import load_filings, roll_up
from ingestion.part_b_xlsx import statement_kind, workbook_paths

H1 = "FINANCIAL_STATEMENTS.XLSX"
Q1 = "FINANCIAL_STATEMENTS (1).XLSX"


@pytest.fixture(scope="module")
def filings():
    return load_filings()


@pytest.fixture(scope="module")
def workbooks(filings):
    return {f.filename: f for f in filings if f.is_workbook}


def test_both_workbooks_are_found_once(filings):
    """Windows globbing is case-insensitive; matching *.xlsx and *.XLSX separately
    once made the manifest count each workbook twice."""
    assert len(workbook_paths()) == 2
    assert len([f for f in filings if f.is_workbook]) == 2


def test_periods_are_read_from_cells_not_filenames(workbooks):
    """Neither filename says anything about a period, and they differ only by '(1)'."""
    h1_periods = " ".join(workbooks[H1].basis.periods_covered)
    q1_periods = " ".join(workbooks[Q1].basis.periods_covered)

    assert "Six-month period ended 30 June 2026" in h1_periods
    assert "Three-month period ended 31 March 2026" in q1_periods
    # Each carries its comparative period too.
    assert "30 June 2025" in h1_periods
    assert "31 March 2025" in q1_periods


def test_the_two_workbooks_are_not_confused_with_each_other(workbooks):
    assert "Three-month" not in " ".join(workbooks[H1].basis.periods_covered)
    assert "Six-month" not in " ".join(workbooks[Q1].basis.periods_covered)


def test_entity_and_currency_come_from_the_cells(workbooks):
    for name in (H1, Q1):
        basis = workbooks[name].basis
        assert basis.company_code == "GC"
        assert basis.legal_entity == "PTT Global Chemical Public Company Limited"
        assert basis.reporting_currency == "THB"


def test_period_end_is_not_mistaken_for_a_fiscal_year_end(workbooks, filings):
    """Regression. 'Three-month period ended 31 March 2026' made the Q1 workbook
    report a 31 March year end — for a company that closes on 31 December, and in a
    dataset whose whole point is that fiscal conventions differ. Workbooks no longer
    infer it; the rollup takes it from a filing that actually states it."""
    assert workbooks[Q1].basis.fiscal_year_end_convention is None
    assert workbooks[H1].basis.fiscal_year_end_convention is None
    assert roll_up(filings)["GC"].fiscal_year_end_convention == "31 December"


def test_sheets_are_classified_by_statement(workbooks):
    sheets = {s.sheet: s.statement for s in workbooks[H1].sheets}
    assert sheets["BS-2-4"] == "balance_sheet"
    assert sheets["PL 5"] == "income_statement"
    assert sheets["EQ 9"] == "changes_in_equity"
    assert sheets["CF 11-12"] == "cash_flow"


def test_sheet_counts_match_the_workbooks(workbooks):
    assert len(workbooks[H1].sheets) == 8
    assert len(workbooks[Q1].sheets) == 6


@pytest.mark.parametrize("name", [H1, Q1])
def test_statement_kind_parses_both_separator_styles(name):
    assert statement_kind("BS-2-4") == "balance_sheet"
    assert statement_kind("PL 5") == "income_statement"
    assert statement_kind("ZZ 1") == "other"


# --- chunking and citation --------------------------------------------------


def test_chunks_cite_a_sheet_and_cell_range(workbooks):
    chunks = chunk_filing(workbooks[H1])
    assert chunks
    chunk = chunks[0]
    assert chunk.sheet == "BS-2-4"
    assert chunk.cell_range and ":" in chunk.cell_range
    assert chunk.citation_token.startswith(f"filing:GC|{H1}|BS-2-4|")


def test_workbook_chunks_are_tables_and_not_embedded(workbooks):
    """Consistent with the rule applied to tables in the PDFs: a vector over a grid of
    numbers carries almost no signal, so these are reached lexically."""
    chunks = chunk_filing(workbooks[H1])
    assert all(c.kind is ChunkKind.TABLE for c in chunks)
    assert all(not c.embeddable for c in chunks)


def test_every_chunk_repeats_the_sheet_header(workbooks):
    """A block of figures must never be separated from the labels that say what they
    are and which period they cover."""
    chunks = chunk_filing(workbooks[Q1])
    for chunk in chunks:
        assert "PTT Global Chemical" in chunk.text


def test_chunk_ids_are_unique_across_both_workbooks(filings):
    chunks = [c for f in filings if f.is_workbook for c in chunk_filing(f)]
    assert len({c.chunk_id for c in chunks}) == len(chunks)


# --- retrieval and resolution -----------------------------------------------


def test_workbook_chunks_reach_the_retrieval_index():
    """The gap this work closed: the workbooks were hashed into the manifest but
    never indexed, so PTTGC's 2026 interim figures were unreachable."""
    from app.services.retrieval import part_b_index

    _, _, chunks, _ = part_b_index()
    from_workbooks = [c for c in chunks if c.sheet is not None]
    assert from_workbooks
    assert {c.filename for c in from_workbooks} == {H1, Q1}


def test_a_sheet_citation_resolves_through_the_api(workbooks):
    chunk = chunk_filing(workbooks[H1])[0]
    source = resolve(chunk.citation_token)
    assert source.resolved is True
    assert source.fields["sheet"] == "BS-2-4"
    assert source.fields["reporting_currency"] == "THB"
    assert "PTT Global Chemical" in (source.text or "")


def test_an_unknown_sheet_does_not_resolve():
    source = resolve(f"filing:GC|{H1}|NOPE 99")
    assert source.resolved is False
    assert "no sheet" in source.error
    assert "BS-2-4" in source.fields["available_sheets"]


def test_page_citations_still_resolve_for_pdfs():
    """Adding sheet locators must not break the existing page form."""
    source = resolve("filing:GC|pttgc-one-report2025-en.pdf|p.140")
    assert source.resolved is True
    assert source.fields["page"] == 140
