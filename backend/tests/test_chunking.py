"""Chunking. Pure functions, so these are fast and exact.

The regression cases at the bottom encode two bugs found by measuring the real
corpus rather than by reasoning about it.
"""

from __future__ import annotations

import pytest

from ingestion.chunking import (
    TARGET_CHARS,
    Chunk,
    ChunkKind,
    chunk_page,
    line_signal,
    page_ends_in_table,
)


def make(text: str, page: int = 1, **kw) -> list[Chunk]:
    return chunk_page(
        text=text, page=page, company_code="RIL", filename="x.pdf",
        doc_type="annual_report", **kw,
    )


# --- line classification ----------------------------------------------------


def test_row_style_table_line_is_strong():
    assert line_signal("Revenue   152,441   132,563") == "strong"
    assert line_signal("EBITDA 56,790 54,446") == "strong"


def test_single_cell_line_is_weak():
    assert line_signal("3,793.72") == "weak"
    assert line_signal("(0.15%)") == "weak"
    assert line_signal("-") == "weak"


def test_ordinary_prose_is_not_table_evidence():
    assert line_signal("The Group recorded a profit for the period.") == "none"
    assert line_signal("Reliance Consumer Products Limited") == "none"
    # A lone number inside a sentence line is not a cell.
    assert line_signal("in the year 2026 the company grew") == "none"


# --- table handling ---------------------------------------------------------


def test_row_style_table_is_one_chunk():
    text = "\n".join([
        "SEGMENT RESULTS",
        "Revenue   152,441   132,563",
        "EBITDA     56,790    54,446",
        "PAT        27,224    26,192",
    ])
    chunks = make(text)
    tables = [c for c in chunks if c.kind is ChunkKind.TABLE]
    assert len(tables) == 1
    for value in ("152,441", "56,790", "27,224"):
        assert value in tables[0].text


def test_cell_per_line_table_is_detected():
    """Regression: PyMuPDF emits many tables one cell per line.

    An earlier detector required two numeric tokens on a line, so these extracted as
    prose and were then sentence-packed -- producing split tables, the exact failure
    this module exists to prevent.
    """
    text = "\n".join([
        "Reliance Consumer Products Limited",
        "0.42%", "3,793.72", "(0.15%)", "(124.30)", "-", "-", "(0.11%)", "142",
        "Dronagiri Navghar South Infra Limited",
        "0.01%", "72.10", "(0.00%)", "(0.03)", "-", "-", "(0.00%)", "39",
    ])
    chunks = make(text)
    assert any(c.kind is ChunkKind.TABLE for c in chunks)
    table = next(c for c in chunks if c.kind is ChunkKind.TABLE)
    assert "3,793.72" in table.text and "72.10" in table.text


def test_short_wrapped_prose_is_not_a_table():
    """Regression: treating every single-token line as table evidence shredded prose
    into 120-character fragments across the whole corpus."""
    text = "\n".join([
        "The Group continued to invest in",
        "downstream capacity during the",
        "period under review, with three",
        "projects reaching completion.",
    ])
    chunks = make(text)
    assert all(c.kind is ChunkKind.PROSE for c in chunks)
    assert len(chunks) == 1


def test_oversized_table_is_kept_whole_and_flagged():
    rows = "\n".join(f"Subsidiary {i} Limited   {i}.00   {i * 11},000   ({i}.5%)" for i in range(400))
    chunks = make(rows)
    tables = [c for c in chunks if c.kind is ChunkKind.TABLE]
    assert len(tables) == 1, "an oversized table must not be split"
    assert tables[0].oversized is True
    assert tables[0].chars > TARGET_CHARS


# --- prose packing ----------------------------------------------------------


def test_prose_respects_the_size_cap():
    text = " ".join(f"Sentence number {i} about operations." for i in range(400))
    chunks = make(text)
    assert all(c.kind is ChunkKind.PROSE for c in chunks)
    assert len(chunks) > 1
    assert all(c.chars <= TARGET_CHARS for c in chunks)


def test_prose_without_sentence_breaks_is_still_capped():
    """Regression: bullet runs and label columns have no terminal punctuation, so the
    sentence splitter could not divide them and chunks ran past the target."""
    text = "\n".join(f"bullet item {i} describing a programme outcome" for i in range(300))
    chunks = make(text)
    assert all(c.chars <= TARGET_CHARS for c in chunks)


def test_heading_travels_with_its_prose():
    text = "\n".join([
        "A1. BASIS OF PREPARATION",
        "The condensed consolidated interim financial statements are unaudited and have",
        "been prepared in accordance with MFRS 134 Interim Financial Reporting.",
    ])
    chunks = make(text)
    assert chunks[0].heading == "A1. BASIS OF PREPARATION"


# --- identity and citation --------------------------------------------------


def test_chunk_ids_are_deterministic_and_unique():
    text = "\n".join([
        "OVERVIEW", "Some narrative about the period.",
        "Revenue   1,000   900", "EBITDA      500   450", "PAT         100    90",
    ])
    first, second = make(text), make(text)
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert len({c.chunk_id for c in first}) == len(first)


def test_citation_resolves_to_a_page():
    chunks = make("Some narrative text about the reporting period.", page=146)
    assert chunks[0].citation_token == "filing:RIL|x.pdf|p.146"


def test_only_prose_is_embeddable():
    text = "\n".join([
        "Narrative describing the segment result for the period.",
        "Revenue   1,000   900", "EBITDA      500   450", "PAT         100    90",
    ])
    chunks = make(text)
    assert any(c.embeddable for c in chunks)
    assert all(not c.embeddable for c in chunks if c.kind is ChunkKind.TABLE)


# --- page boundaries and continuation ---------------------------------------


def test_page_ending_in_table_is_detected_through_footer():
    """Regression: these pages end with running footers, so requiring the very last
    line to be table content found zero continuations in the entire corpus."""
    text = "\n".join([
        "Revenue   1,000   900",
        "EBITDA      500   450",
        "PAT         100    90",
        "Note: Above highlights are part of Management Discussion",
        "274",
    ])
    assert page_ends_in_table(text) is True


def test_page_ending_in_prose_is_not_a_table_end():
    assert page_ends_in_table("The Group expects conditions to remain stable.") is False


def test_continuation_flag_set_on_first_table_chunk_after_header():
    """Pages open with running headers, which are prose -- so the flag belongs on the
    first TABLE chunk of the page, not the first chunk."""
    text = "\n".join([
        "Consolidated Financial Statements",
        "Reliance Industries Limited",
        "Revenue   1,000   900",
        "EBITDA      500   450",
        "PAT         100    90",
    ])
    chunks = make(text, previous_page_ended_in_table=True)
    table = next(c for c in chunks if c.kind is ChunkKind.TABLE)
    assert table.continues_from_previous_page is True
    assert all(not c.continues_from_previous_page for c in chunks if c.kind is ChunkKind.PROSE)


def test_no_continuation_when_previous_page_was_prose():
    text = "Revenue   1,000   900\nEBITDA      500   450\nPAT         100    90"
    chunks = make(text, previous_page_ended_in_table=False)
    assert all(not c.continues_from_previous_page for c in chunks)


def test_empty_page_yields_nothing():
    assert make("   \n  \n") == []


@pytest.mark.parametrize("page", [1, 42, 207])
def test_chunks_never_span_pages(page):
    chunks = make("Narrative text for this page about operations.", page=page)
    assert all(c.page == page for c in chunks)
