"""The HTTP layer, including the citation resolver the UI depends on."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.domain.enums import Domain
from app.domain.plan import NotePlan, RouteDecision, SectionPlan
from app.graph.schemas import DraftedClaim, DraftedSection, GatherDecision, ToolCall
from app.main import app
from app.providers.azure_llm import ScriptedLLM, set_llm


@pytest.fixture(autouse=True)
def inprocess(monkeypatch):
    monkeypatch.setattr(get_settings(), "mcp_inprocess", True, raising=False)
    yield
    set_llm(None)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# --- health -----------------------------------------------------------------


def test_health_reports_mode_honestly(client):
    body = client.get("/api/health").json()
    assert body["mcp_reachable"] is True
    assert len(body["mcp_tools"]) == 5
    # No credentials in the test environment, so this must not claim hybrid retrieval.
    assert body["retrieval_mode"] in {"hybrid", "lexical_only"}
    assert body["index_manifest"]


def test_corpus_endpoint_lists_real_filter_values(client):
    body = client.get("/api/corpus").json()
    assert "Southeast Asia" in body["register"]["values"]["region"]
    assert "Propylene|Southeast Asia CFR|Contract" in body["price_series"]


# --- citation resolution ----------------------------------------------------


def test_plant_citation_resolves(client):
    body = client.get("/api/sources", params={"token": "plant:PL-001"}).json()
    assert body["resolved"] is True
    assert "Cilegon Cracker 1" in body["title"]
    assert body["fields"]["capacity_kta"] == "1,200 kta"


def test_withheld_capacity_resolves_without_inventing_a_number(client):
    body = client.get("/api/sources", params={"token": "plant:PL-008"}).json()
    assert body["resolved"] is True
    assert body["fields"]["capacity_kta"] == "Not publicly confirmed"


def test_price_citation_resolves_with_its_basis(client):
    token = "price:Propylene|Southeast Asia CFR|2026-04|Contract"
    body = client.get("/api/sources", params={"token": token}).json()
    assert body["resolved"] is True
    assert "Contract basis" in body["subtitle"]


def test_price_citation_on_the_wrong_basis_does_not_resolve(client):
    token = "price:Propylene|Southeast Asia CFR|2026-04|Spot"
    body = client.get("/api/sources", params={"token": token}).json()
    assert body["resolved"] is False
    assert "Spot basis" in body["error"]


def test_document_citation_returns_full_text_and_provenance(client):
    token = "doc:002_asean_monitor_capacity_note_2026-06-02.md"
    body = client.get("/api/sources", params={"token": token}).json()
    assert body["resolved"] is True
    assert body["fields"]["independent"] is True
    assert "1,050 kta" in body["text"]
    assert "does not publish" in body["fields"]["scope_limitation"]


def test_superseded_document_says_so_when_resolved(client):
    token = "doc:008_mekong_vungtau_schedule_2026-01-28.md"
    body = client.get("/api/sources", params={"token": token}).json()
    assert "startup_year" in body["fields"]["superseded_on"]
    assert "2027" in body["fields"]["superseded_on"]


def test_filing_citation_carries_basis_of_preparation(client):
    token = "filing:PETRONAS|PETRONAS Interim Financial Report 1H 2026 28.8.2026.pdf|p.2"
    body = client.get("/api/sources", params={"token": token}).json()
    assert body["resolved"] is True
    assert body["fields"]["reporting_currency"] == "MYR"
    assert body["fields"]["legal_entity"] == "Petroliam Nasional Berhad (PETRONAS Group)"
    assert "not the entity named by its folder" in body["fields"]["warning"]


def test_invented_citation_does_not_resolve(client):
    body = client.get("/api/sources", params={"token": "plant:PL-999"}).json()
    assert body["resolved"] is False
    assert "PL-999" in body["error"]


# --- running a brief --------------------------------------------------------


SUPPLY = SectionPlan(
    id="s1", kind="supply_base", title="Supply",
    questions=["Which units?"], intended_tools=["query_plant_register"],
    tool_rationale="The register lists them.", unanswerable_if="Capacity is withheld.",
)


def _scripted(claims: list[DraftedClaim]) -> ScriptedLLM:
    seen: set[str] = set()

    def gather(user: str, schema):
        if "s1" in seen:
            return GatherDecision(action="done", reason="enough")
        seen.add("s1")
        return GatherDecision(
            action="call_tool",
            tool_call=ToolCall(
                tool="query_plant_register",
                arguments={"region": "Southeast Asia", "product": "Ethylene",
                           "status": ["Operating"], "aggregate": True},
                why="totals",
            ),
            reason="gathering",
        )

    return ScriptedLLM({
        "route": lambda u, s: RouteDecision(domain=Domain.PART_A, reasoning="plants"),
        "plan": lambda u, s: NotePlan(brief="b", domain=Domain.PART_A, sections=[SUPPLY]),
        "gather.s1": gather,
        "draft.s1": lambda u, s: DraftedSection(claims=claims),
    })


def test_post_then_poll_returns_a_cited_note(client):
    set_llm(_scripted([
        DraftedClaim(
            text="Operating ethylene capacity totals 5,370 kta.",
            citations=[{"kind": "plant", "plant_id": "PL-001"}],
        )
    ]))
    accepted = client.post("/api/notes", json={"brief": "Southeast Asia ethylene supply"})
    assert accepted.status_code == 202
    run_id = accepted.json()["run_id"]

    # The stream drives the run to completion.
    with client.stream("GET", f"/api/notes/{run_id}/stream") as response:
        assert response.status_code == 200
        kinds = [
            line[len("event: "):]
            for line in response.iter_lines()
            if line.startswith("event: ")
        ]

    assert "plan" in kinds
    assert "tool_call" in kinds
    assert "note" in kinds
    assert kinds[-1] == "closed"

    body = client.get(f"/api/notes/{run_id}").json()
    assert body["status"] == "complete"
    claims = body["note"]["sections"][0]["claims"]
    assert claims[0]["citations"][0]["plant_id"] == "PL-001"


def test_every_citation_in_a_note_resolves_through_the_api(client):
    """End to end: what the note renders, a reader can check."""
    set_llm(_scripted([
        DraftedClaim(
            text="Operating ethylene capacity totals 5,370 kta.",
            citations=[{"kind": "plant", "plant_id": "PL-001"}],
        )
    ]))
    run_id = client.post("/api/notes", json={"brief": "supply"}).json()["run_id"]
    with client.stream("GET", f"/api/notes/{run_id}/stream") as response:
        list(response.iter_lines())

    note = client.get(f"/api/notes/{run_id}").json()["note"]
    tokens = []
    for section in note["sections"]:
        for claim in section["claims"]:
            for citation in claim["citations"]:
                kind = citation["kind"]
                if kind == "plant":
                    tokens.append(f"plant:{citation['plant_id']}")

    assert tokens
    for token in tokens:
        assert client.get("/api/sources", params={"token": token}).json()["resolved"]


def test_refused_brief_is_a_note_not_an_error(client):
    set_llm(ScriptedLLM({
        "route": lambda u, s: RouteDecision(
            domain=None, reasoning="mixes datasets", out_of_scope=True,
            requires_both_domains=True,
            out_of_scope_reason="The brief compares a synthetic operator with a real filer.",
        )
    }))
    run_id = client.post(
        "/api/notes", json={"brief": "Compare Nusantara Olefins with Reliance"}
    ).json()["run_id"]
    with client.stream("GET", f"/api/notes/{run_id}/stream") as response:
        list(response.iter_lines())

    body = client.get(f"/api/notes/{run_id}").json()
    assert body["status"] == "complete"
    assert body["note"]["refusal"]["reason"] == "cross_domain"


def test_unknown_run_is_404(client):
    assert client.get("/api/notes/doesnotexist").status_code == 404
