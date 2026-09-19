"""Index manifest and the ingestion validation gate.
"""

from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from pydantic import BaseModel, ConfigDict
from app.config import get_settings
from app.domain.enums import Basis, PriceRegion, Product
from app.logging import get_logger

log = get_logger(__name__)

CHUNKER_VERSION = "1"
INGEST_VERSION = "1"
SEA_CFR = PriceRegion.SOUTHEAST_ASIA_CFR
NEA_CFR = PriceRegion.NORTHEAST_ASIA_CFR
ASIA_CFR = PriceRegion.ASIA_CFR
SPOT = Basis.SPOT
CONTRACT = Basis.CONTRACT

EXPECTED_SERIES: dict[tuple[str, str, str], int] = {
    (Product.ETHYLENE, NEA_CFR, SPOT): 32,
    (Product.ETHYLENE, SEA_CFR, SPOT): 32,
    (Product.HDPE, SEA_CFR, SPOT): 30,
    (Product.NAPHTHA, ASIA_CFR, SPOT): 32,
    (Product.PP, SEA_CFR, SPOT): 32,
    (Product.PROPYLENE, SEA_CFR, SPOT): 24,
    (Product.PROPYLENE, SEA_CFR, CONTRACT): 8,
}


class FileStamp(BaseModel):
    model_config = ConfigDict(frozen=True)
    path: str
    sha256: str
    bytes: int


class Manifest(BaseModel):
    model_config = ConfigDict(frozen=True)
    built_at: str
    ingest_version: str
    chunker_version: str
    embedding_model: str | None
    part_a: tuple[FileStamp, ...]
    part_b: tuple[FileStamp, ...]
    counts: dict[str, int]

    @property
    def corpus_fingerprint(self) -> str:
        """One hash over every source file, for stamping onto a note."""
        joined = "|".join(s.sha256 for s in (*self.part_a, *self.part_b))
        return hashlib.sha256(joined.encode()).hexdigest()[:16]


def _stamp(path: Path, root: Path) -> FileStamp:
    data = path.read_bytes()
    return FileStamp(
        path=str(path.relative_to(root)).replace("\\", "/"),
        sha256=hashlib.sha256(data).hexdigest(),
        bytes=len(data),
    )


def validate() -> list[str]:
    """Run every shape check. Returns problems; empty means the corpus is as expected."""
    from app.services.datasets import load_plants, load_prices
    from ingestion.part_a_docs import load_documents
    from ingestion.supersession import load_supersession
    from ingestion.supersession import validate as validate_supersession

    settings = get_settings()
    problems: list[str] = []

    plants = load_plants()
    if len(plants) != settings.expect_plant_rows:
        problems.append(f"plants.csv has {len(plants)} rows, expected {settings.expect_plant_rows}")

    prices = load_prices()
    if len(prices) != settings.expect_price_rows:
        problems.append(f"prices.csv has {len(prices)} rows, expected {settings.expect_price_rows}")

    docs = load_documents()
    if len(docs) != settings.expect_document_count:
        problems.append(
            f"documents/ has {len(docs)} files, expected {settings.expect_document_count}"
        )

    # The series inventory, including the deliberate gaps and the basis break.
    actual: dict[tuple[str, str, str], int] = {}
    for row in prices:
        key = (row.product, row.region, row.basis)
        actual[key] = actual.get(key, 0) + 1
    for key, expected in EXPECTED_SERIES.items():
        if actual.get(key) != expected:
            problems.append(
                f"series {key} has {actual.get(key, 0)} rows, expected {expected}"
            )
    for key in set(actual) - set(EXPECTED_SERIES):
        problems.append(f"unexpected series in prices.csv: {key}")

    # Nulls are load-bearing in this corpus, so their count is pinned.
    blank_capacity = [p.plant_id for p in plants if p.capacity_kta is None]
    if blank_capacity != ["PL-008"]:
        problems.append(f"expected exactly PL-008 to have blank capacity, got {blank_capacity}")

    blank_price = [(r.product, r.month) for r in prices if r.price is None]
    if sorted(blank_price) != [("PP", "2026-07"), ("PP", "2026-08")]:
        problems.append(f"expected exactly two blank PP prices, got {blank_price}")

    problems.extend(validate_supersession(load_supersession(), settings.documents_dir))
    return problems


def build(embedding_model: str | None = None) -> Manifest:
    settings = get_settings()
    root = settings.part_a_dir.parent

    part_a = [settings.plants_csv, settings.prices_csv, *sorted(settings.documents_dir.glob("*.md"))]
    part_b = sorted(
        {
            p.resolve()
            for pattern in ("*.pdf", "*.xlsx")
            for p in settings.part_b_dir.rglob(pattern)
        }
    )

    manifest = Manifest(
        built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ingest_version=INGEST_VERSION,
        chunker_version=CHUNKER_VERSION,
        embedding_model=embedding_model,
        part_a=tuple(_stamp(p, root) for p in part_a),
        part_b=tuple(_stamp(p, root) for p in part_b),
        counts={
            "plants": settings.expect_plant_rows,
            "prices": settings.expect_price_rows,
            "documents": settings.expect_document_count,
            "part_b_files": len(part_b),
        },
    )
    return manifest


def write(manifest: Manifest, path: Path | None = None) -> Path:
    path = path or (get_settings().index_dir / "manifest.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
    log.info("manifest.written", path=str(path), fingerprint=manifest.corpus_fingerprint)
    return path
