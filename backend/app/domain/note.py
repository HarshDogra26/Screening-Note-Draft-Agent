from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .citation import Citation
from .enums import ClaimStatus, Domain, GapReason, RefusalReason, SectionKind


class NumericValue(BaseModel):

    model_config = ConfigDict(frozen=True)
    value: float
    unit: str | None = None
    as_written: str


class DerivedCalc(BaseModel):

    formula: str
    inputs: list[Citation] = Field(min_length=1)
    result: float
    unit: str | None = None


class Claim(BaseModel):
    text: str = Field(min_length=1)
    citations: list[Citation] = Field(min_length=1)
    status: ClaimStatus = ClaimStatus.ASSERTED
    numeric_values: list[NumericValue] = Field(default_factory=list)
    derived: DerivedCalc | None = None

    @property
    def citation_tokens(self) -> list[str]:
        return [c.token for c in self.citations]


class AttributedPosition(BaseModel):
    """One side of a disagreement, with who says it and why."""

    source_label: str
    value: str
    stated_reason: str | None = None
    citations: list[Citation] = Field(min_length=1)


class Conflict(BaseModel):

    model_config = ConfigDict(extra="forbid")
    subject: str
    positions: list[AttributedPosition] = Field(min_length=2)
    note: str = "This note does not resolve the difference."


class Gap(BaseModel):

    subject: str
    reason: GapReason
    detail: str
    citations: list[Citation] = Field(default_factory=list)


class Refusal(BaseModel):

    reason: RefusalReason
    detail: str
    citations: list[Citation] = Field(default_factory=list)


class Section(BaseModel):
    id: str
    kind: SectionKind
    title: str
    claims: list[Claim] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    coverage_note: str | None = None


class ScreeningNote(BaseModel):
    run_id: str
    brief: str
    domain: Domain
    plan: dict = Field(default_factory=dict)
    sections: list[Section] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    refusal: Refusal | None = None
    index_manifest: dict = Field(default_factory=dict)
    tool_calls: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def _refusal_or_content(self) -> ScreeningNote:
        if self.refusal is None and not self.sections:
            raise ValueError("a note must have either sections or a refusal")
        return self

    @property
    def all_claims(self) -> list[Claim]:
        return [c for s in self.sections for c in s.claims]

    @property
    def all_citations(self) -> list[Citation]:
        return [cit for c in self.all_claims for cit in c.citations]
