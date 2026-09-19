"""Curated supersession edges.
"""

from __future__ import annotations
from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict
from app.logging import get_logger

log = get_logger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parent / "supersession.yaml"


class SupersessionEdge(BaseModel):
    model_config = ConfigDict(frozen=True)
    superseded: str
    superseded_by: str
    company: str
    scope: tuple[str, ...]
    strength: str
    evidence: str
    superseded_values: dict[str, str] = {}
    current_values: dict[str, str] = {}
    still_current_in_superseded: tuple[str, ...] = ()
    register_agrees: str | None = None
    note: str | None = None


class SupersessionIndex(BaseModel):
    model_config = ConfigDict(frozen=True)

    edges: tuple[SupersessionEdge, ...] = ()

    def superseding(self, filename: str) -> list[SupersessionEdge]:
        """Edges where this document is the one being superseded."""
        return [e for e in self.edges if e.superseded == filename]

    def supersedes(self, filename: str) -> list[SupersessionEdge]:
        """Edges where this document is the one doing the superseding."""
        return [e for e in self.edges if e.superseded_by == filename]

    def is_superseded_for(self, filename: str, field: str) -> bool:
        return any(field in e.scope for e in self.superseding(filename))

    def note_for(self, filename: str) -> str | None:
        """Prose a drafter can use to surface a revision rather than hide it."""
        edges = self.supersedes(filename)
        if not edges:
            return None
        parts = []
        for edge in edges:
            for field, old in edge.superseded_values.items():
                new = edge.current_values.get(field, "the current figure")
                parts.append(f"{field.replace('_', ' ')} revised from {old} to {new}")
        if not parts:
            return None
        return "; ".join(parts)


def load_supersession(path: Path | None = None) -> SupersessionIndex:
    path = path or DEFAULT_PATH
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    index = SupersessionIndex(edges=tuple(raw.get("edges", [])))
    log.info(
        "supersession.loaded",
        n=len(index.edges),
        edges=[f"{e.superseded} -> {e.superseded_by}" for e in index.edges],
    )
    return index


def validate(index: SupersessionIndex, documents_dir: Path) -> list[str]:
    """Check the curation still matches the corpus. Returns problems, empty if clean."""
    on_disk = {p.name for p in documents_dir.glob("*.md")}
    problems: list[str] = []
    for edge in index.edges:
        for side in (edge.superseded, edge.superseded_by):
            if side not in on_disk:
                problems.append(f"supersession.yaml references missing document: {side}")
        if edge.superseded >= edge.superseded_by:
            problems.append(
                f"{edge.superseded_by} does not sort after {edge.superseded}; "
                "a document cannot supersede a later one"
            )
        if not edge.scope:
            problems.append(f"{edge.superseded} -> {edge.superseded_by} has empty scope; "
                            "a document-wide supersession discards still-current facts")
    return problems
