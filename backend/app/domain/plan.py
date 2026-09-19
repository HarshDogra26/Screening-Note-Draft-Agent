"""The plan.
"""

from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field
from .enums import Domain, SectionKind


class SectionPlan(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    kind: SectionKind
    title: str
    questions: list[str] = Field(min_length=1)
    intended_tools: list[str] = Field(min_length=1)
    tool_rationale: str
    unanswerable_if: str


class ExcludedTopic(BaseModel):

    model_config = ConfigDict(frozen=True)
    topic: str
    reason: str


class NotePlan(BaseModel):
    model_config = ConfigDict(frozen=True)
    brief: str
    domain: Domain
    sections: list[SectionPlan] = Field(min_length=1)
    excluded: list[ExcludedTopic] = Field(default_factory=list)

    @property
    def tools_intended(self) -> set[str]:
        return {tool for section in self.sections for tool in section.intended_tools}


class RouteDecision(BaseModel):

    model_config = ConfigDict(frozen=True)
    domain: Domain | None
    reasoning: str
    out_of_scope: bool = False
    out_of_scope_reason: str | None = None
    requires_both_domains: bool = False
