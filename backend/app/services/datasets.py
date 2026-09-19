"""Loading plants.csv and prices.csv.
"""

from __future__ import annotations
import csv
from functools import lru_cache
from pathlib import Path
from pydantic import BaseModel, ConfigDict
from ..config import get_settings
from ..logging import get_logger

log = get_logger(__name__)


class Plant(BaseModel):
    model_config = ConfigDict(frozen=True)
    plant_id: str
    plant_name: str
    company: str
    city: str
    country: str
    region: str
    product: str
    capacity_kta: float | None  
    process_route: str
    startup_year: int
    status: str
    complex_id: str
    source_type: str

    @property
    def capacity_is_public(self) -> bool:
        return self.capacity_kta is not None


class PriceRow(BaseModel):
    model_config = ConfigDict(frozen=True)
    month: str  
    product: str
    region: str
    price: float | None  
    unit: str
    basis: str
    source_type: str


def _blank_to_none(value: str) -> str | None:
    value = value.strip()
    return value or None


def _parse_float(value: str) -> float | None:
    cleaned = _blank_to_none(value)
    if cleaned is None:
        return None
    return float(cleaned.replace(",", ""))


def load_plants(path: Path | None = None) -> tuple[Plant, ...]:
    path = path or get_settings().plants_csv
    rows: list[Plant] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for raw in csv.DictReader(fh):
            rows.append(
                Plant(
                    plant_id=raw["plant_id"].strip(),
                    plant_name=raw["plant_name"].strip(),
                    company=raw["company"].strip(),
                    city=raw["city"].strip(),
                    country=raw["country"].strip(),
                    region=raw["region"].strip(),
                    product=raw["product"].strip(),
                    capacity_kta=_parse_float(raw["capacity_kta"]),
                    process_route=raw["process_route"].strip(),
                    startup_year=int(raw["startup_year"].strip()),
                    status=raw["status"].strip(),
                    complex_id=raw["complex_id"].strip(),
                    source_type=raw["source_type"].strip(),
                )
            )
    out = tuple(sorted(rows, key=lambda p: p.plant_id))
    log.info(
        "datasets.plants_loaded",
        n=len(out),
        blank_capacity=[p.plant_id for p in out if p.capacity_kta is None],
    )
    return out


def load_prices(path: Path | None = None) -> tuple[PriceRow, ...]:
    path = path or get_settings().prices_csv
    rows: list[PriceRow] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for raw in csv.DictReader(fh):
            month = raw["month"].strip()
            # The file stores the first day of the month; the domain works in YYYY-MM.
            if len(month) == 10:
                month = month[:7]
            rows.append(
                PriceRow(
                    month=month,
                    product=raw["product"].strip(),
                    region=raw["region"].strip(),
                    price=_parse_float(raw["price"]),
                    unit=raw["unit"].strip(),
                    basis=raw["basis"].strip(),
                    source_type=raw["source_type"].strip(),
                )
            )
    out = tuple(sorted(rows, key=lambda r: (r.product, r.region, r.month, r.basis)))
    log.info(
        "datasets.prices_loaded",
        n=len(out),
        blank_price=[f"{r.product}|{r.region}|{r.month}" for r in out if r.price is None],
    )
    return out


@lru_cache(maxsize=1)
def plants() -> tuple[Plant, ...]:
    return load_plants()


@lru_cache(maxsize=1)
def prices() -> tuple[PriceRow, ...]:
    return load_prices()
