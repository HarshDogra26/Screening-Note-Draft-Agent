"""Price series continuity.
"""

from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field
from .enums import ComparabilityVerdict, WarningCode


class ComparabilityWarning(BaseModel):
    model_config = ConfigDict(frozen=True)
    code: WarningCode
    detail: str
    corroborating_document: str | None = None


class BasisSegment(BaseModel):
    """A contiguous run of months assessed on one basis."""

    model_config = ConfigDict(frozen=True)
    basis: str
    month_from: str
    month_to: str
    n: int
    first: float | None = None
    last: float | None = None
    min: float | None = None
    max: float | None = None
    mean: float | None = None


class SeriesContinuity(BaseModel):
    model_config = ConfigDict(frozen=True)
    requested_from: str
    requested_to: str
    months_requested: int
    months_present: int
    missing_months: tuple[str, ...] = ()
    unconfirmed_months: tuple[str, ...] = ()
    basis_segment_count: int = 1

    @property
    def has_gap(self) -> bool:
        return bool(self.missing_months)

    @property
    def has_basis_change(self) -> bool:
        return self.basis_segment_count > 1


class PricePoint(BaseModel):
    model_config = ConfigDict(frozen=True)
    month: str
    product: str
    region: str
    basis: str
    price: float | None
    unit: str
    source_type: str

    @property
    def citation_token(self) -> str:
        return f"price:{self.product}|{self.region}|{self.month}|{self.basis}"


class SeriesResult(BaseModel):

    kind: str = "price_series"
    product: str
    region: str
    points: list[PricePoint] = Field(default_factory=list)
    continuity: SeriesContinuity
    verdict: ComparabilityVerdict
    comparability_warnings: list[ComparabilityWarning] = Field(default_factory=list)
    basis_segments: list[BasisSegment] = Field(default_factory=list)
    change: float | None = None
    change_pct: float | None = None
    change_refused_reason: str | None = None

    @property
    def citations(self) -> list[str]:
        return [p.citation_token for p in self.points]
