"""LangSmith tracing.
"""

from __future__ import annotations
from collections.abc import Callable
from typing import Any, TypeVar
from ..logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")
_warned = False


def tracing_enabled() -> bool:
    """Whether LangSmith is configured, accepting both naming schemes."""
    import os

    flag = os.getenv("LANGSMITH_TRACING") or os.getenv("LANGCHAIN_TRACING_V2") or ""
    key = os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY") or ""
    return flag.lower() in {"1", "true", "yes"} and bool(key)


def wrap_client(client: T, *, name: str) -> T:
    """Wrap an OpenAI client so its calls appear as LLM spans with token usage.
    """
    global _warned
    if not tracing_enabled():
        return client
    try:
        from langsmith.wrappers import wrap_openai

        wrapped = wrap_openai(client, chat_name=name)
        log.info("tracing.client_wrapped", name=name)
        return wrapped
    except Exception as exc:  # noqa: BLE001
        if not _warned:
            log.warning(
                "tracing.wrap_failed",
                error=str(exc)[:200],
                effect="runs continue untraced; graph structure may still be captured",
            )
            _warned = True
        return client


def traced(name: str, run_type: str = "chain") -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that names a span, and is a no-op when tracing is off.
    """

    def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        try:
            from langsmith import traceable

            return traceable(name=name, run_type=run_type)(func)
        except Exception:  # noqa: BLE001
            return func

    return decorate


def run_metadata(run_id: str, brief: str) -> dict[str, Any]:
    """Metadata attached to a run so a trace can be found from an eval result."""
    return {
        "run_id": run_id,
        "brief": brief[:200],
    }
