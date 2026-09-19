from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field
from ..domain.citation import Citation
from ..domain.enums import ClaimStatus, GapReason


class ToolCall(BaseModel):
    tool: str
    arguments: dict = Field(default_factory=dict)
    why: str


class GatherDecision(BaseModel):
    """One step of the gather loop: call something, or stop."""

    action: Literal["call_tool", "done"]
    tool_call: ToolCall | None = None
    reason: str


class DraftedClaim(BaseModel):

    text: str
    citations: list[Citation] = Field(default_factory=list)
    status: ClaimStatus = ClaimStatus.ASSERTED


class DraftedGap(BaseModel):
    subject: str
    reason: GapReason
    detail: str
    citations: list[Citation] = Field(default_factory=list)


class DraftedSection(BaseModel):
    claims: list[DraftedClaim] = Field(default_factory=list)
    gaps: list[DraftedGap] = Field(default_factory=list)
    coverage_note: str | None = None
