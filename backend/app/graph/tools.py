"""The orchestrator's MCP client.
"""

from __future__ import annotations
import json
import re
from typing import Any

from ..config import get_settings
from ..logging import get_logger

log = get_logger(__name__)

TOOL_NAMES = (
    "describe_corpus",
    "query_plant_register",
    "query_price_series",
    "search_part_a_documents",
    "search_part_b_filings",
)

DOMAIN_TOOLS: dict[str, frozenset[str]] = {
    "part_a": frozenset({
        "describe_corpus",
        "query_plant_register",
        "query_price_series",
        "search_part_a_documents",
    }),
    "part_b": frozenset({"describe_corpus", "search_part_b_filings"}),
}


def _any_of_type(spec: dict[str, Any]) -> str:
    """Render an optional parameter's type, which MCP emits as anyOf[T, null]."""
    options = [o.get("type") for o in spec.get("anyOf", []) if o.get("type") != "null"]
    return options[0] if options else "string"


QUOTED_VALUES_RE = re.compile(r"'([^']{2,60})'")


def _allowed_values(spec: dict[str, Any]) -> str | None:
    """Enumerated values for a parameter, from its schema or its description.

    The tool descriptions spell the permitted values out in quotes ("'Southeast Asia
    CFR', 'Northeast Asia CFR' or 'Asia CFR'"), so they can be lifted without
    duplicating them in a second place that could drift.
    """
    enum = spec.get("enum")
    if enum:
        return ", ".join(repr(v) for v in enum)

    description = spec.get("description") or ""

    if re.search(r"\be\.g\.|for example|such as", description, re.I):
        return None

    listed = re.search(
        r"'[^']{2,60}'(?:\s*,\s*'[^']{2,60}')*\s*(?:,|\bor\b)\s*'[^']{2,60}'",
        description,
    )
    if not listed:
        return None
    quoted = QUOTED_VALUES_RE.findall(listed.group(0))
    return ", ".join(repr(v) for v in quoted) if len(quoted) >= 2 else None


def _unwrap(result: Any) -> dict[str, Any]:
    if getattr(result, "structured_content", None):
        content = result.structured_content
        if "result" in content:
            return content["result"]
        return content
    blocks = getattr(result, "content", None) or []
    if blocks and getattr(blocks[0], "text", None):
        return json.loads(blocks[0].text)
    return {}


class ToolSession:
    """An open MCP session. Use as an async context manager."""

    def __init__(self, target: Any, domain: str | None = None) -> None:
        self._target = target
        self._domain = domain
        self._client = None
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> ToolSession:
        from fastmcp import Client

        self._client = Client(self._target)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client is not None:
            await self._client.__aexit__(*exc)

    async def list_tools(self) -> list[str]:
        assert self._client is not None
        return [t.name for t in await self._client.list_tools()]

    async def describe_tools(self) -> str:
        assert self._client is not None
        allowed = DOMAIN_TOOLS.get(self._domain or "", None)
        lines: list[str] = []
        for tool in await self._client.list_tools():
            if allowed is not None and tool.name not in allowed:
                continue
            schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {}
            properties = schema.get("properties", {}) or {}
            required = set(schema.get("required", []) or [])
            params = []
            notes: list[str] = []
            for name, spec in properties.items():
                kind = spec.get("type") or _any_of_type(spec)
                flag = "required" if name in required else "optional"
                params.append(f"{name}: {kind} ({flag})")
                hint = _allowed_values(spec)
                if hint:
                    notes.append(f"    {name} must be one of: {hint}")
            lines.append(f"- {tool.name}({', '.join(params) or 'no arguments'})")
            lines.extend(notes)
        return "\n".join(lines)

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert self._client is not None
        if name not in TOOL_NAMES:
            return {"kind": "error", "error": f"unknown tool '{name}'",
                    "available": list(TOOL_NAMES)}

        allowed = DOMAIN_TOOLS.get(self._domain or "", None)
        if allowed is not None and name not in allowed:
            log.warning("tool.blocked_cross_domain", tool=name, domain=self._domain)
            return {
                "kind": "error",
                "error": f"'{name}' belongs to the other dataset and cannot be used here",
                "detail": (
                    f"This note is a {self._domain} note. The synthetic plant-and-price "
                    "world and the real company filings are never combined."
                ),
                "available": sorted(allowed),
            }

        arguments = {k: v for k, v in arguments.items() if v is not None}

        try:
            raw = await self._client.call_tool(name, arguments)
            payload = _unwrap(raw)
        except Exception as exc: 
            payload = {"kind": "error", "error": f"{type(exc).__name__}: {exc}"[:400]}

        record = {
            "tool": name,
            "arguments": arguments,
            "kind": payload.get("kind", "unknown"),
            "n": payload.get("n"),
        }
        if payload.get("kind") == "error":
            record["error"] = str(payload.get("error", ""))[:300]
            log.warning("tool.error", **record)
        else:
            log.info("tool.call", **record)
        self.calls.append(record)
        return payload


def session(domain: str | None = None) -> ToolSession:
    """In-process for tests, real HTTP otherwise.

    Pass the routed domain to restrict which tools the session will call.
    """
    settings = get_settings()
    if settings.mcp_inprocess:
        from mcp_servers.screening_server import mcp

        return ToolSession(mcp, domain=domain)
    return ToolSession(settings.mcp_url, domain=domain)
