"""Part A document ingestion.
"""

from __future__ import annotations
import re
from enum import StrEnum
from pathlib import Path
from pydantic import BaseModel, ConfigDict
from app.config import get_settings
from app.logging import get_logger

log = get_logger(__name__)

FILENAME_RE = re.compile(r"^(?P<num>\d{3})_(?P<slug>.+)_(?P<date>\d{4}-\d{2}-\d{2})\.md$")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


class NegativeKind(StrEnum):
    SCOPE_LIMITATION = "scope_limitation"
    NOT_DISCLOSED = "not_disclosed"
    OUT_OF_SCOPE = "out_of_scope"

NEGATIVE_PATTERNS: list[tuple[NegativeKind, re.Pattern[str]]] = [
    (
        NegativeKind.SCOPE_LIMITATION,
        re.compile(
            r"does not (publish|contain)\b|cannot be answered|"
            r"does not .{0,40}\b(forecast|outlook)",
            re.I,
        ),
    ),
    (
        NegativeKind.OUT_OF_SCOPE,
        re.compile(r"does not operate\b", re.I),
    ),
    (
        NegativeKind.NOT_DISCLOSED,
        re.compile(
            r"did not (disclose|state|provide)\b|does not disclose\b|"
            r"(were|was) not disclosed\b|has not disclosed\b|"
            r"no .{0,40}has been (set|fixed|taken)\b|"
            r"will not comment\b",
            re.I,
        ),
    ),
]

SUPERSESSION_EXPLICIT_RE = re.compile(r"\bsupersed(?:es|ing)\b|\breplaces the\b", re.I)
SUPERSESSION_REVISION_RE = re.compile(r"\b(?:has revised|revises)\b", re.I)


class NegativeStatement(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: NegativeKind
    sentence: str


class SupersessionSignal(BaseModel):
    """Evidence that a document supersedes an earlier one.
    """

    model_config = ConfigDict(frozen=True)
    strength: str 
    sentence: str


class Document(BaseModel):
    model_config = ConfigDict(frozen=True)
    filename: str
    doc_number: int
    date: str
    title: str
    source_type: str
    company: str | None = None
    publisher: str | None = None
    body: str
    entities: tuple[str, ...] = ()
    negative_statements: tuple[NegativeStatement, ...] = ()
    supersession_candidate: SupersessionSignal | None = None

    @property
    def attribution(self) -> str:
        """Who is speaking. In this corpus that is half of every fact."""
        return self.company or self.publisher or "unknown"

    @property
    def citation_token(self) -> str:
        return f"doc:{self.filename}"

    def has(self, kind: NegativeKind) -> bool:
        return any(n.kind is kind for n in self.negative_statements)


def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Split flat `key: value` front matter from the body.
    """
    if not text.startswith("---"):
        return {}, text.strip()
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text.strip()

    meta: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip().lower()] = value.strip()
    return meta, parts[2].strip()


def _sentences(body: str) -> list[str]:
    flat = re.sub(r"\s+", " ", body.replace("**", "")).strip()
    return [s.strip() for s in SENTENCE_RE.split(flat) if s.strip()]


def _find_negatives(body: str) -> tuple[NegativeStatement, ...]:
    found: list[NegativeStatement] = []
    for sentence in _sentences(body):
        for kind, pattern in NEGATIVE_PATTERNS:
            if pattern.search(sentence):
                found.append(NegativeStatement(kind=kind, sentence=sentence))
                break
    return tuple(found)


def _find_entities(text: str, vocabulary: tuple[str, ...]) -> tuple[str, ...]:
    lowered = text.lower()
    return tuple(sorted({term for term in vocabulary if term.lower() in lowered}))


def _find_supersession(body: str) -> SupersessionSignal | None:
    """Prefer an explicit statement over revision language; a document may have both."""
    revision: SupersessionSignal | None = None
    for sentence in _sentences(body):
        if SUPERSESSION_EXPLICIT_RE.search(sentence):
            return SupersessionSignal(strength="explicit", sentence=sentence)
        if revision is None and SUPERSESSION_REVISION_RE.search(sentence):
            revision = SupersessionSignal(strength="revision", sentence=sentence)
    return revision


def entity_vocabulary() -> tuple[str, ...]:
    """Companies, plant names and cities from the register, for entity linking."""
    from app.services.datasets import plants

    terms: set[str] = set()
    for plant in plants():
        terms.add(plant.company)
        terms.add(plant.plant_name)
        terms.add(plant.city)
    return tuple(sorted(terms))


def parse_document(path: Path, vocabulary: tuple[str, ...] | None = None) -> Document:
    match = FILENAME_RE.match(path.name)
    if not match:
        raise ValueError(f"unexpected document filename: {path.name}")

    meta, body = _parse_front_matter(path.read_text(encoding="utf-8"))
    vocabulary = vocabulary if vocabulary is not None else entity_vocabulary()

    date = meta.get("date", match.group("date"))
    if date != match.group("date"):
        log.warning(
            "docs.date_mismatch", filename=path.name, front_matter=date,
            filename_date=match.group("date"),
        )

    return Document(
        filename=path.name,
        doc_number=int(match.group("num")),
        date=date,
        title=meta.get("title", ""),
        source_type=meta.get("source_type", ""),
        company=meta.get("company"),
        publisher=meta.get("publisher"),
        body=body,
        entities=_find_entities(f"{meta.get('title', '')} {body}", vocabulary),
        negative_statements=_find_negatives(body),
        supersession_candidate=_find_supersession(body),
    )


def load_documents(directory: Path | None = None) -> tuple[Document, ...]:
    directory = directory or get_settings().documents_dir
    vocabulary = entity_vocabulary()
    docs = tuple(
        sorted(
            (parse_document(p, vocabulary) for p in directory.glob("*.md")),
            key=lambda d: d.doc_number,
        )
    )
    log.info(
        "docs.loaded",
        n=len(docs),
        scope_limited=[d.filename for d in docs if d.has(NegativeKind.SCOPE_LIMITATION)],
        supersession_candidates=[d.filename for d in docs if d.supersession_candidate],
    )
    return docs
