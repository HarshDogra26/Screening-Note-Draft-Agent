"""API request and response shapes."""

from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field

class BriefRequest(BaseModel):
    brief: str = Field(min_length=3, max_length=500)


class RunAccepted(BaseModel):
    run_id: str
    status: Literal["running"] = "running"
    stream_url: str


class RunStatus(BaseModel):
    run_id: str
    brief: str
    status: Literal["running", "complete", "failed"]
    note: dict[str, Any] | None = None
    dropped_claims: list[dict[str, Any]] = Field(default_factory=list)
    violations: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class SourceResponse(BaseModel):
    """A resolved citation.
    """

    token: str
    kind: str
    resolved: bool
    title: str | None = None
    subtitle: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    text: str | None = None
    error: str | None = None


class HealthResponse(BaseModel):
    status: str
    provider_mode: str
    retrieval_mode: str
    mcp_reachable: bool
    tracing: bool = False
    tracing_project: str | None = None
    mcp_tools: list[str] = Field(default_factory=list)
    index_manifest: dict[str, Any] = Field(default_factory=dict)
    missing_credentials: list[str] = Field(default_factory=list)
