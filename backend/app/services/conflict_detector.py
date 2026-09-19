"""Detecting disagreements between sources.
"""

from __future__ import annotations
import re
from pydantic import BaseModel, ConfigDict
from ..domain.citation import DocumentCitation, PlantCitation
from ..domain.note import AttributedPosition, Conflict
from ..logging import get_logger

log = get_logger(__name__)

CAPACITY_RE = re.compile(r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*kta\b", re.I)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

PRODUCT_WORDS: dict[str, str] = {
    "ethylene": "Ethylene",
    "propylene": "Propylene",
    "hdpe": "HDPE",
    "ldpe": "LDPE",
    "lldpe": "LLDPE",
    "polypropylene": "PP",
    "pp": "PP",
    "cracker": "Ethylene",
}

PRODUCT_WINDOW_CHARS = 60

NON_CLAIM_MARKERS = re.compile(
    r"\bdesign(ed)?\b|\btarget\b|\bproposed\b|\bstudy\b|\bpre-FEED\b|\bno .{0,30}fixed\b",
    re.I,
)


class CapacityMention(BaseModel):
    model_config = ConfigDict(frozen=True)
    filename: str
    attribution: str
    is_independent: bool
    value: float
    product: str | None
    plant_id: str | None
    sentence: str


def _sentences(text: str) -> list[str]:
    flat = re.sub(r"\s+", " ", text.replace("**", "")).strip()
    return [s.strip() for s in SENTENCE_RE.split(flat) if s.strip()]


def _nearest_product(position: int, hits: list[tuple[int, str]]) -> str | None:
    """Which product a capacity figure refers to.
    """
    candidates = [
        (abs(at - position), 0 if at >= position else 1, product)
        for at, product in hits
        if abs(at - position) <= PRODUCT_WINDOW_CHARS
    ]
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


def capacity_mentions(doc, plants) -> list[CapacityMention]:
    """Capacity figures in a document, linked to a plant where unambiguous."""
    by_name = {p.plant_name.lower(): p for p in plants}
    doc_plants = [p for p in plants if p.plant_name in doc.entities or p.company in doc.entities]

    out: list[CapacityMention] = []
    for sentence in _sentences(doc.body):
        lowered = sentence.lower()
        product_hits = [
            (m.start(), PRODUCT_WORDS[word])
            for word in PRODUCT_WORDS
            for m in re.finditer(rf"\b{word}\b", lowered)
        ]
        for match in CAPACITY_RE.finditer(sentence):
            value = float(match.group(1).replace(",", ""))
            product = _nearest_product(match.start(), product_hits)

            plant = next((p for name, p in by_name.items() if name in lowered), None)

            if plant is None and product is not None:
                candidates = [p for p in doc_plants if p.product == product]
                if len(candidates) == 1:
                    plant = candidates[0]

            out.append(
                CapacityMention(
                    filename=doc.filename,
                    attribution=doc.attribution,
                    is_independent=doc.publisher is not None,
                    value=value,
                    product=product,
                    plant_id=plant.plant_id if plant else None,
                    sentence=sentence,
                )
            )
    return out


def detect_capacity_conflicts(documents, plants) -> list[Conflict]:
    by_id = {p.plant_id: p for p in plants}

    claims: dict[str, dict[float, list[CapacityMention]]] = {}
    for doc in documents:
        for mention in capacity_mentions(doc, plants):
            if mention.plant_id is None:
                continue
            if NON_CLAIM_MARKERS.search(mention.sentence):
                continue
            claims.setdefault(mention.plant_id, {}).setdefault(mention.value, []).append(mention)

    conflicts: list[Conflict] = []
    for plant_id, by_value in sorted(claims.items()):
        plant = by_id[plant_id]
        register = plant.capacity_kta
        values = set(by_value)
        if register is not None:
            values.add(register)
        if len(values) < 2:
            continue

        positions: list[AttributedPosition] = []

        if register is not None:
            corroborating = by_value.get(register, [])
            sources = [PlantCitation(plant_id=plant_id, field="capacity_kta")]
            sources.extend(DocumentCitation(filename=m.filename) for m in corroborating)
            label = "Plant register"
            if corroborating:
                label += f" and {corroborating[0].attribution}"
            positions.append(
                AttributedPosition(
                    source_label=label,
                    value=f"{register:,.0f} kta",
                    stated_reason=corroborating[0].sentence if corroborating else None,
                    citations=sources,
                )
            )

        for value, mentions in sorted(by_value.items()):
            if register is not None and value == register:
                continue
            first = mentions[0]
            positions.append(
                AttributedPosition(
                    source_label=first.attribution,
                    value=f"{value:,.0f} kta",
                    stated_reason=first.sentence,
                    citations=[DocumentCitation(filename=m.filename) for m in mentions],
                )
            )

        if len(positions) < 2:
            continue

        conflicts.append(
            Conflict(
                subject=f"{plant.plant_name} ({plant.company}) {plant.product} capacity",
                positions=positions,
            )
        )
        log.info(
            "conflict.detected",
            plant_id=plant_id,
            values=[p.value for p in positions],
            sources=[p.source_label for p in positions],
        )

    return conflicts
