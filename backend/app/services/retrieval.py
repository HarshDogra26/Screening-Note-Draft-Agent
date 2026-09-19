"""Retrieval over the two document corpora.
"""

from __future__ import annotations
import re
from collections.abc import Sequence
from functools import lru_cache
from typing import Generic, TypeVar
from rank_bm25 import BM25Okapi
from ..logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")
TOKEN_RE = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    """a an and are as at be by for from has have in is it its of on or that the to
    was were will with said company said's said. this these those which""".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]


class BM25Index(Generic[T]):
    """Lexical index with deterministic ordering.
    """

    def __init__(self, items: Sequence[T], text_of, tie_key) -> None:
        self._items = list(items)
        self._tie_key = tie_key
        corpus = [tokenize(text_of(item)) for item in self._items]
        self._bm25 = BM25Okapi(corpus) if corpus else None

    def search(self, query: str, k: int = 5, predicate=None) -> list[tuple[T, float]]:
        if self._bm25 is None:
            return []
        tokens = tokenize(query)
        scores = self._bm25.get_scores(tokens) if tokens else [0.0] * len(self._items)

        scored = [
            (item, float(score))
            for item, score in zip(self._items, scores, strict=True)
            if predicate is None or predicate(item)
        ]
        scored.sort(key=lambda pair: (-pair[1], self._tie_key(pair[0])))
        return scored[:k]


def rrf_fuse(
    rankings: Sequence[Sequence[T]], tie_key, k: int = 5, rrf_k: int = 60
) -> list[T]:
    """Reciprocal rank fusion with a fixed constant and deterministic tie-breaking.
    """
    scores: dict = {}
    seen: dict = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            key = tie_key(item)
            scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + rank)
            seen.setdefault(key, item)
    ordered = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
    return [seen[key] for key, _ in ordered[:k]]


# --- Part A ----------------------------------------------------------------


@lru_cache(maxsize=1)
def part_a_index():
    from ingestion.part_a_docs import load_documents

    docs = load_documents()
    index = BM25Index(
        docs,
        text_of=lambda d: f"{d.title} {d.attribution} {' '.join(d.entities)} {d.body}",
        tie_key=lambda d: d.doc_number,
    )
    log.info("retrieval.part_a_index_built", n=len(docs))
    return docs, index


# --- Part B ----------------------------------------------------------------


@lru_cache(maxsize=1)
def part_b_index():
    """Chunk-level index over the filings.
    """
    from ingestion.chunking import chunk_filing
    from ingestion.part_b_filings import load_filings, roll_up

    filings = load_filings()
    chunks = [c for f in filings for c in chunk_filing(f)]
    index = BM25Index(
        chunks,
        text_of=lambda c: f"{c.heading or ''}\n{c.text}",
        tie_key=lambda c: c.chunk_id,
    )
    log.info(
        "retrieval.part_b_index_built",
        filings=len(filings),
        chunks=len(chunks),
        prose=sum(1 for c in chunks if c.embeddable),
    )
    return filings, roll_up(filings), chunks, index


@lru_cache(maxsize=1)
def _dense_index():
    """The vector index, or None. Absence is normal, not an error."""
    from ingestion.manifest import CHUNKER_VERSION

    from .embeddings import get_embedder
    from .vectorstore import open_index

    embedder = get_embedder()
    if embedder is None:
        return None, None
    index = open_index(embedder.model, CHUNKER_VERSION)
    if index is None or index.count == 0:
        log.info("retrieval.dense_empty", effect="lexical-only until `ingestion.cli embed` runs")
        return None, None
    return embedder, index


async def search_part_b(query: str, k: int, predicate=None) -> tuple[list, str]:
    """Hybrid search over Part B chunks.
    """
    _, _, chunks, bm25 = part_b_index()
    lexical = [c for c, _ in bm25.search(query, k=k * 2, predicate=predicate)]

    embedder, dense = _dense_index()
    if embedder is None or dense is None:
        return lexical[:k], "lexical_only"

    by_id = {c.chunk_id: c for c in chunks}
    try:
        vector = (await embedder.embed([query]))[0]
        dense_ids = dense.query(vector, k=k * 2)
    except Exception as exc:  # noqa: BLE001
        log.warning("retrieval.dense_failed", error=str(exc)[:200])
        return lexical[:k], "lexical_only_dense_failed"

    dense_hits = [by_id[i] for i in dense_ids if i in by_id]
    if predicate is not None:
        dense_hits = [c for c in dense_hits if predicate(c)]

    fused = rrf_fuse([lexical, dense_hits], tie_key=lambda c: c.chunk_id, k=k)
    return fused, "hybrid"
