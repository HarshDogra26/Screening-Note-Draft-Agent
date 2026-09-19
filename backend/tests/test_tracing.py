"""Tracing is optional and must never break a run.

The failure mode being guarded against is a misconfigured LangSmith key taking down
note generation. Losing observability is acceptable; losing the note is not.
"""

from __future__ import annotations

import pytest

from app.providers import tracing


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2",
        "LANGSMITH_API_KEY", "LANGCHAIN_API_KEY",
        "LANGSMITH_PROJECT", "LANGCHAIN_PROJECT",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


def test_disabled_by_default():
    assert tracing.tracing_enabled() is False


def test_flag_without_a_key_is_not_enabled(monkeypatch):
    """A flag set but no key would otherwise look enabled and silently send nothing."""
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    assert tracing.tracing_enabled() is False


def test_both_naming_schemes_are_accepted(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGCHAIN_API_KEY", "lsv2_pt_test")
    assert tracing.tracing_enabled() is True

    monkeypatch.delenv("LANGCHAIN_TRACING_V2")
    monkeypatch.delenv("LANGCHAIN_API_KEY")
    monkeypatch.setenv("LANGSMITH_TRACING", "1")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_test")
    assert tracing.tracing_enabled() is True


def test_falsey_flag_values_do_not_enable(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_API_KEY", "lsv2_pt_test")
    for value in ("false", "0", "no", ""):
        monkeypatch.setenv("LANGCHAIN_TRACING_V2", value)
        assert tracing.tracing_enabled() is False


def test_wrap_client_is_a_passthrough_when_disabled():
    sentinel = object()
    assert tracing.wrap_client(sentinel, name="X") is sentinel


def test_wrap_client_returns_the_client_when_wrapping_fails(monkeypatch):
    """A bad key or SDK change must degrade to untraced, not raise."""
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGCHAIN_API_KEY", "lsv2_pt_test")

    sentinel = object()  # not an OpenAI client, so wrap_openai will reject it
    assert tracing.wrap_client(sentinel, name="X") is sentinel


def test_traced_decorator_preserves_behaviour():
    @tracing.traced("thing")
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, 3) == 5


@pytest.mark.asyncio
async def test_traced_decorator_works_on_async_functions():
    @tracing.traced("async-thing")
    async def add(a: int, b: int) -> int:
        return a + b

    assert await add(2, 3) == 5


def test_trace_config_is_empty_when_disabled():
    from app.graph.builder import _trace_config

    assert _trace_config("abc123", "a brief") == {}


def test_trace_config_names_the_run_and_tags_it(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGCHAIN_API_KEY", "lsv2_pt_test")
    from app.graph.builder import _trace_config

    config = _trace_config("abc123", "Screening note on Southeast Asia ethylene supply")
    assert config["run_name"].startswith("Screening note")
    assert config["metadata"]["run_id"] == "abc123"
    assert "screening-note" in config["tags"]


def test_run_metadata_truncates_a_long_brief():
    metadata = tracing.run_metadata("r1", "x" * 500)
    assert len(metadata["brief"]) == 200
