"""Citations.
"""

from __future__ import annotations
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field
from .enums import SourceKind


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True)


class PlantCitation(_Base):
    kind: Literal[SourceKind.PLANT] = SourceKind.PLANT
    plant_id: str = Field(pattern=r"^PL-\d{3}$")
    field: str | None = None

    @property
    def token(self) -> str:
        return f"plant:{self.plant_id}"


class PriceCitation(_Base):
    kind: Literal[SourceKind.PRICE] = SourceKind.PRICE
    product: str
    region: str
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    basis: str

    @property
    def token(self) -> str:
        return f"price:{self.product}|{self.region}|{self.month}|{self.basis}"


class DocumentCitation(_Base):
    kind: Literal[SourceKind.DOCUMENT] = SourceKind.DOCUMENT
    filename: str
    quote: str | None = None

    @property
    def token(self) -> str:
        return f"doc:{self.filename}"


class FilingCitation(_Base):
    kind: Literal[SourceKind.FILING] = SourceKind.FILING
    company: str
    filename: str
    page: int | None = None
    sheet: str | None = None
    cell_range: str | None = None

    @property
    def token(self) -> str:
        locator = ""
        if self.page is not None:
            locator = f"|p.{self.page}"
        elif self.sheet is not None:
            locator = f"|{self.sheet}"
            if self.cell_range:
                locator += f"|{self.cell_range}"
        return f"filing:{self.company}|{self.filename}{locator}"


Citation = Annotated[
    Union[PlantCitation, PriceCitation, DocumentCitation, FilingCitation],
    Field(discriminator="kind"),
]

DOMAIN_ALLOWED_KINDS: dict[str, frozenset[SourceKind]] = {
    "part_a": frozenset({SourceKind.PLANT, SourceKind.PRICE, SourceKind.DOCUMENT}),
    "part_b": frozenset({SourceKind.FILING}),
}
