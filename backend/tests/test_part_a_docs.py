"""Part A document ingestion, checked against what the documents actually say."""

from __future__ import annotations

import pytest

from ingestion.part_a_docs import NegativeKind, load_documents


@pytest.fixture(scope="module")
def docs():
    return load_documents()


@pytest.fixture(scope="module")
def by_number(docs):
    return {d.doc_number: d for d in docs}


def test_all_documents_parse(docs):
    assert len(docs) == 27
    assert [d.doc_number for d in docs] == list(range(1, 28))
    assert all(d.title for d in docs)
    assert all(d.source_type for d in docs)
    # Every document has exactly one speaker, never both and never neither.
    assert all((d.company is None) != (d.publisher is None) for d in docs)


def test_source_types_are_the_three_expected(docs):
    assert {d.source_type for d in docs} == {
        "Company press release",
        "Industry association bulletin",
        "Government agency publication",
    }


def test_publishers(by_number):
    assert by_number[2].publisher == "ASEAN Petrochemical Monitor"
    assert by_number[13].publisher == "ASEAN Petrochemical Monitor"
    assert by_number[25].publisher == "ASEAN Petrochemical Monitor"
    assert by_number[22].publisher == "Regional Energy Statistics Office"
    assert by_number[27].publisher == "Regional Energy Statistics Office"
    assert by_number[1].company == "Nusantara Olefins"


# --- the three kinds of negative statement are kept distinct ----------------


def test_scope_limitations_ground_refusals(by_number):
    """The publisher does not cover this class of question at all."""
    scoped = {
        n for n in range(1, 28) if by_number[n].has(NegativeKind.SCOPE_LIMITATION)
    }
    assert scoped == {2, 13, 22, 25}

    forecast = next(
        n
        for n in by_number[25].negative_statements
        if n.kind is NegativeKind.SCOPE_LIMITATION and "forecast" in n.sentence.lower()
    )
    assert "does not publish price forecasts or outlooks" in forecast.sentence

    totals = by_number[13].negative_statements[0]
    assert "aggregate regional capacity totals" in totals.sentence


def test_not_disclosed_grounds_gaps_not_refusals(by_number):
    """A specific figure was withheld. Different from the publisher not covering it."""
    withheld = {n for n in range(1, 28) if by_number[n].has(NegativeKind.NOT_DISCLOSED)}
    assert {1, 10, 11, 17, 21, 24} <= withheld

    # doc 001: the capital cost of the Cilegon expansion
    assert any(
        "capital cost" in n.sentence
        for n in by_number[1].negative_statements
        if n.kind is NegativeKind.NOT_DISCLOSED
    )
    # doc 011: corroborates PL-008's blank capacity
    assert any(
        "does not disclose nameplate capacity" in n.sentence
        for n in by_number[11].negative_statements
    )


def test_out_of_scope_entity(by_number):
    """Straits Advanced Materials is a compounder and is not in the register."""
    assert by_number[19].has(NegativeKind.OUT_OF_SCOPE)
    assert "does not operate crackers" in by_number[19].negative_statements[0].sentence


def test_categories_do_not_overlap_incorrectly(by_number):
    """doc 011 withholds a figure; it does not limit the publisher's scope."""
    assert by_number[11].has(NegativeKind.NOT_DISCLOSED)
    assert not by_number[11].has(NegativeKind.SCOPE_LIMITATION)

    # doc 025 limits scope AND is a normal commentary; it withholds no figure.
    assert by_number[25].has(NegativeKind.SCOPE_LIMITATION)


def test_factual_negatives_are_not_flagged(by_number):
    """Statements that merely contain 'not' are not scope limitations.

    doc 016 "no capacity additions are planned at Tianjin before 2029" and doc 026
    "does not expect the work to affect nameplate capacity" are ordinary facts. If
    these were flagged, the agent would refuse to answer answerable briefs.
    """
    assert not by_number[16].has(NegativeKind.SCOPE_LIMITATION)
    assert not by_number[16].has(NegativeKind.OUT_OF_SCOPE)
    assert not by_number[26].has(NegativeKind.SCOPE_LIMITATION)
    assert not by_number[26].has(NegativeKind.NOT_DISCLOSED)


# --- supersession: explicit language only -----------------------------------


def test_supersession_detected_from_explicit_language(by_number):
    """doc 009 says so in as many words, and that sentence is the one captured.

    doc 009 contains BOTH "has revised the expected startup ... from 2027 to 2028"
    and "This announcement supersedes the schedule guidance issued by the company in
    January 2026". The explicit sentence is the stronger evidence and must win.
    """
    signal = by_number[9].supersession_candidate
    assert signal is not None
    assert signal.strength == "explicit"
    assert "supersedes the schedule guidance" in signal.sentence


def test_dissent_is_not_treated_as_supersession(by_number):
    """The load-bearing negative test.

    doc 002 (2026-06-02) is LATER than doc 001 (2026-03-11) and disagrees with it on
    Cilegon capacity. A recency heuristic would mark doc 001 superseded and the
    1,200/1,050 conflict would vanish. It must not be proposed.
    """
    assert by_number[2].supersession_candidate is None
    assert by_number[1].supersession_candidate is None


def test_supersession_candidates_are_few_and_explicit(docs):
    candidates = {d.doc_number for d in docs if d.supersession_candidate}
    # Only documents that actually use supersession language. Anything beyond this
    # set means the pattern has started over-reaching.
    assert candidates == {9}


# --- entity linking ---------------------------------------------------------


def test_entities_link_to_the_register(by_number):
    assert "Nusantara Olefins" in by_number[1].entities
    assert "Cilegon Cracker 1" in by_number[1].entities
    assert "Coral Bay Olefins" in by_number[11].entities


def test_out_of_register_company_has_no_register_entity(by_number):
    """Straits Advanced Materials is not in plants.csv, so nothing should link."""
    assert "Straits Advanced Materials" not in by_number[19].entities
