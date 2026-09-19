"""Embeddings, with a content-addressed cache and honest degradation.
"""

from __future__ import annotations
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from ..config import get_settings
from ..logging import get_logger
from ..providers.base import ProviderError, RateLimitError, TransientProviderError
from ..providers.retry import with_retry

log = get_logger(__name__)


def cache_key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\x00{text}".encode()).hexdigest()


class EmbeddingCache:
    """One JSON file per vector. Simple, inspectable, and safe across processes."""

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> list[float] | None:
        path = self._dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def put(self, key: str, vector: Sequence[float]) -> None:
        tmp = self._dir / f"{key}.tmp"
        tmp.write_text(json.dumps(list(vector)), encoding="utf-8")
        tmp.replace(self._dir / f"{key}.json")

    def __len__(self) -> int:
        return sum(1 for _ in self._dir.glob("*.json"))


class AzureEmbedder:
    def __init__(self, *, endpoint: str, api_key: str, deployment: str, api_version: str,
                 cache: EmbeddingCache) -> None:
        from openai import AsyncAzureOpenAI

        from ..providers.tracing import wrap_client

        client = AsyncAzureOpenAI(
            azure_endpoint=endpoint, api_key=api_key, api_version=api_version
        )
        self._client = wrap_client(client, name="AzureEmbeddings")
        self._deployment = deployment
        self._cache = cache
        self.model = deployment

    async def _embed_uncached(self, texts: list[str]) -> list[list[float]]:
        settings = get_settings()

        async def call():
            try:
                response = await self._client.embeddings.create(
                    model=self._deployment, input=texts
                )
            except Exception as exc:  # noqa: BLE001 - mapped to the error taxonomy
                name = type(exc).__name__
                if "RateLimit" in name:
                    raise RateLimitError(str(exc)) from exc
                if any(k in name for k in ("APIConnection", "Timeout", "InternalServer")):
                    raise TransientProviderError(str(exc)) from exc
                raise ProviderError(str(exc)) from exc
            return [item.embedding for item in response.data]

        return await with_retry(
            call,
            attempts=settings.max_retries,
            base_delay_s=settings.retry_base_delay_s,
            label="embeddings",
        )

    async def embed(self, texts: Sequence[str], batch_size: int = 64) -> list[list[float]]:
        """Embed in corpus order, hitting the API only for genuine misses."""
        out: list[list[float] | None] = [None] * len(texts)
        misses: list[int] = []

        for i, text in enumerate(texts):
            hit = self._cache.get(cache_key(self.model, text))
            if hit is None:
                misses.append(i)
            else:
                out[i] = hit

        log.info("embeddings.cache", total=len(texts), hits=len(texts) - len(misses),
                 misses=len(misses))

        for start in range(0, len(misses), batch_size):
            batch = misses[start : start + batch_size]
            vectors = await self._embed_uncached([texts[i] for i in batch])
            for i, vector in zip(batch, vectors, strict=True):
                self._cache.put(cache_key(self.model, texts[i]), vector)
                out[i] = vector

        assert all(v is not None for v in out)
        return [v for v in out if v is not None]


def get_embedder() -> AzureEmbedder | None:
    """The embedder, or None when the system should run lexical-only."""
    settings = get_settings()
    missing = settings.missing_live_credentials()
    if not settings.is_live or missing:
        log.info(
            "embeddings.disabled",
            provider_mode=settings.provider_mode.value,
            missing_credentials=missing,
            effect="retrieval will run lexical-only",
        )
        return None
    return AzureEmbedder(
        endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key.get_secret_value(),
        deployment=settings.azure_openai_embedding_deployment,
        api_version=settings.azure_openai_embedding_api_version,
        cache=EmbeddingCache(settings.chroma_dir.parent / "embedding_cache"),
    )
