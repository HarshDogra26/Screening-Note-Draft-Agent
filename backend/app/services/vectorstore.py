"""Chroma wrapper for the Part B prose chunks.
"""

from __future__ import annotations
from collections.abc import Sequence
from pathlib import Path
from ..config import get_settings
from ..logging import get_logger

log = get_logger(__name__)

PART_B_COLLECTION = "part_b_prose"


def collection_name(embedding_model: str, chunker_version: str) -> str:
    """Versioned so a chunker or model change cannot silently reuse stale vectors."""
    safe = embedding_model.replace("/", "_").replace(".", "_")
    return f"{PART_B_COLLECTION}__{safe}__v{chunker_version}"


class VectorIndex:
    def __init__(self, directory: Path, name: str) -> None:
        import chromadb

        self._client = chromadb.PersistentClient(path=str(directory))
        self._name = name
        self._collection = self._client.get_or_create_collection(
            name=name,
            metadata={
                "hnsw:space": "cosine",
                # Search the whole graph. Removes ANN variance between runs.
                "hnsw:search_ef": 4096,
                "hnsw:construction_ef": 512,
                "hnsw:M": 64,
            },
        )

    @property
    def count(self) -> int:
        return self._collection.count()

    def upsert(
        self,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        documents: Sequence[str],
        metadatas: Sequence[dict],
        batch_size: int = 512,
    ) -> None:
        for start in range(0, len(ids), batch_size):
            stop = start + batch_size
            self._collection.upsert(
                ids=list(ids[start:stop]),
                embeddings=[list(v) for v in vectors[start:stop]],
                documents=list(documents[start:stop]),
                metadatas=list(metadatas[start:stop]),
            )
        log.info("vectorstore.upserted", collection=self._name, n=len(ids), total=self.count)

    def query(self, vector: Sequence[float], k: int, where: dict | None = None) -> list[str]:
        """Return chunk ids, best first. Ties broken by id so ordering is stable."""
        if self.count == 0:
            return []
        result = self._collection.query(
            query_embeddings=[list(vector)],
            n_results=min(k, self.count),
            where=where or None,
            include=["distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        paired = sorted(zip(ids, distances, strict=True), key=lambda p: (p[1], p[0]))
        return [chunk_id for chunk_id, _ in paired]

    def reset(self) -> None:
        self._client.delete_collection(self._name)
        self._collection = self._client.get_or_create_collection(name=self._name)


def open_index(embedding_model: str, chunker_version: str) -> VectorIndex | None:
    """Open the collection, or None if Chroma is unavailable.
    """
    settings = get_settings()
    try:
        return VectorIndex(
            settings.chroma_dir, collection_name(embedding_model, chunker_version)
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vectorstore.unavailable", error=str(exc)[:200],
                    effect="retrieval will run lexical-only")
        return None
