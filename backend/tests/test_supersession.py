"""Supersession is field-scoped and never inferred from recency."""

from __future__ import annotations

import pytest

from app.config import get_settings
from ingestion.supersession import load_supersession, validate


@pytest.fixture(scope="module")
def index():
    return load_supersession()


def test_curation_matches_the_corpus(index):
    assert validate(index, get_settings().documents_dir) == []


def test_vung_tau_schedule_superseded(index):
    edges = index.superseding("008_mekong_vungtau_schedule_2026-01-28.md")
    assert len(edges) == 1
    edge = edges[0]
    assert edge.superseded_by == "009_mekong_vungtau_revised_2026-07-09.md"
    assert edge.strength == "explicit"
    assert edge.superseded_values["startup_year"] == "2027"
    assert edge.current_values["startup_year"] == "2028"
    assert edge.register_agrees == "PL-006"


def test_superseded_document_keeps_its_still_current_facts(index):
    """Doc 008's 750 kta design capacity is not superseded by the schedule change."""
    edge = index.superseding("008_mekong_vungtau_schedule_2026-01-28.md")[0]
    assert index.is_superseded_for(edge.superseded, "startup_year")
    assert not index.is_superseded_for(edge.superseded, "ethylene_capacity")
    assert any("750 kta" in fact for fact in edge.still_current_in_superseded)


def test_pp3_update_is_scoped_to_schedule_only(index):
    """Doc 007 revises mechanical completion and nothing else.

    Doc 007 says the capital estimate is unchanged and the existing lines are
    unaffected. Marking doc 006 wholly superseded would discard the 300 kta line
    capacity and the FID, both of which are still current.
    """
    edge = index.superseding("006_andaman_pp3_fid_2026-01-15.md")[0]
    assert edge.scope == ("mechanical_completion_schedule",)
    assert edge.strength == "implicit"
    assert edge.superseded_values["mechanical_completion"] == "Q2 2028"
    assert edge.current_values["mechanical_completion"] == "Q4 2028"
    assert any("300 kta" in fact for fact in edge.still_current_in_superseded)
    assert not index.is_superseded_for(edge.superseded, "line_capacity")


def test_cilegon_dissent_is_not_a_supersession_edge(index):
    """The load-bearing negative test.

    Doc 002 is later than doc 001 and disagrees on Cilegon capacity. If it appeared
    here as an edge, the 1,200-vs-1,050 conflict would be silently resolved in favour
    of whichever document is newer -- which is exactly the behaviour the brief asks us
    not to have.
    """
    assert index.superseding("001_nusantara_cilegon_expansion_2026-03-11.md") == []
    assert index.supersedes("002_asean_monitor_capacity_note_2026-06-02.md") == []
    filenames = {e.superseded for e in index.edges} | {e.superseded_by for e in index.edges}
    assert "002_asean_monitor_capacity_note_2026-06-02.md" not in filenames


def test_revision_note_surfaces_the_change(index):
    note = index.note_for("009_mekong_vungtau_revised_2026-07-09.md")
    assert note is not None
    assert "2027" in note and "2028" in note

    note7 = index.note_for("007_andaman_pp3_delay_2026-07-30.md")
    assert "Q2 2028" in note7 and "Q4 2028" in note7


def test_every_edge_has_a_scope(index):
    """An unscoped edge is a document-wide supersession, which loses facts."""
    assert all(e.scope for e in index.edges)
