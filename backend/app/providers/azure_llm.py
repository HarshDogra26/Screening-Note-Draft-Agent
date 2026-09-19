"""LLM access.
"""

from __future__ import annotations
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TypeVar
from pydantic import BaseModel
from ..config import get_settings
from ..logging import get_logger
from .base import ProviderError, RateLimitError, TerminalProviderError, TransientProviderError
from .retry import with_retry

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLM(Protocol):
    async def structured(self, *, label: str, system: str, user: str, schema: type[T]) -> T: ...


def call_key(model: str, system: str, user: str, schema: type[BaseModel]) -> str:
    payload = json.dumps(
        {
            "model": model,
            "system": system,
            "user": user,
            "schema": schema.__name__,
            "shape": schema.model_json_schema(),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class AzureLLM:
    def __init__(self) -> None:
        from openai import AsyncAzureOpenAI

        from .tracing import wrap_client

        settings = get_settings()
        self._settings = settings
        client = AsyncAzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key.get_secret_value(),
            api_version=settings.azure_openai_chat_api_version,
        )
        self._client = wrap_client(client, name="AzureChat")
        self.model = settings.azure_openai_chat_deployment

    async def structured(self, *, label: str, system: str, user: str, schema: type[T]) -> T:
        settings = self._settings

        from langsmith import get_current_run_tree

        try:
            run_tree = get_current_run_tree()
            if run_tree is not None:
                run_tree.add_metadata({"call": label, "schema": schema.__name__})
        except Exception:  # noqa: BLE001 - tracing must never break a run
            pass

        async def call() -> T:
            try:
                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=settings.llm_temperature,
                    top_p=settings.llm_top_p,
                    seed=settings.llm_seed,
                    timeout=settings.llm_timeout_s,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema.__name__,
                            "strict": False,
                            "schema": schema.model_json_schema(),
                        },
                    },
                )
            except Exception as exc: 
                name = type(exc).__name__
                if "RateLimit" in name:
                    raise RateLimitError(str(exc)) from exc
                if any(k in name for k in ("APIConnection", "Timeout", "InternalServer")):
                    raise TransientProviderError(str(exc)) from exc
                raise TerminalProviderError(str(exc)) from exc

            content = response.choices[0].message.content or ""
            try:
                return schema.model_validate_json(content)
            except Exception as exc:  # noqa: BLE001
                raise TransientProviderError(
                    f"{label}: response did not match {schema.__name__}: {exc}"
                ) from exc

        result = await with_retry(
            call,
            attempts=settings.max_retries,
            base_delay_s=settings.retry_base_delay_s,
            label=f"llm.{label}",
        )
        log.info("llm.call", label=label, schema=schema.__name__, model=self.model)
        return result


class CassetteLLM:
    """Record/replay wrapper.
    """

    def __init__(self, directory: Path, inner: LLM | None = None, *, record: bool = False) -> None:
        self._dir = directory
        self._dir.mkdir(parents=True, exist_ok=True)
        self._inner = inner
        self._record = record
        self.model = getattr(inner, "model", "cassette")

    async def structured(self, *, label: str, system: str, user: str, schema: type[T]) -> T:
        key = call_key(self.model, system, user, schema)
        path = self._dir / f"{label}.{key[:16]}.json"

        if path.exists():
            log.info("llm.cassette_hit", label=label, key=key[:16])
            return schema.model_validate_json(path.read_text(encoding="utf-8"))

        if not self._record or self._inner is None:
            raise TerminalProviderError(
                f"no cassette for {label} ({key[:16]}). Run with PROVIDER_MODE=live and "
                "recording enabled to capture it."
            )

        result = await self._inner.structured(label=label, system=system, user=user, schema=schema)
        path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        log.info("llm.cassette_recorded", label=label, key=key[:16])
        return result


class ScriptedLLM:
    """Canned structured responses, keyed by call label. For tests.
    """

    def __init__(self, handlers: dict[str, Callable[[str, type[BaseModel]], Any]]) -> None:
        self._handlers = handlers
        self.calls: list[tuple[str, str]] = []
        self.model = "scripted"

    async def structured(self, *, label: str, system: str, user: str, schema: type[T]) -> T:
        self.calls.append((label, user))
        handler = self._handlers.get(label)
        if handler is None:
            raise TerminalProviderError(f"ScriptedLLM has no handler for '{label}'")
        result = handler(user, schema)
        if not isinstance(result, schema):
            raise ProviderError(f"handler for '{label}' returned {type(result).__name__}")
        return result


_llm: LLM | None = None


def set_llm(llm: LLM | None) -> None:
    """Injection point for tests and for cassette replay."""
    global _llm
    _llm = llm


def get_llm() -> LLM:
    global _llm
    if _llm is not None:
        return _llm

    settings = get_settings()
    cassettes = settings.cassette_dir
    if settings.is_live:
        missing = settings.missing_live_credentials()
        if missing:
            raise TerminalProviderError(f"PROVIDER_MODE=live but missing: {', '.join(missing)}")
        _llm = CassetteLLM(cassettes, AzureLLM(), record=True)
    else:
        _llm = CassetteLLM(cassettes, None, record=False)
    return _llm
