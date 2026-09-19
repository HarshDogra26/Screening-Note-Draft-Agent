"""The plant register engine.
"""

from __future__ import annotations
from collections.abc import Sequence
from ..logging import get_logger
from .datasets import Plant, plants

log = get_logger(__name__)

FILTERABLE = (
    "company",
    "country",
    "region",
    "product",
    "status",
    "process_route",
    "complex_id",
    "city",
)


def _matches(plant: Plant, key: str, wanted: str | Sequence[str]) -> bool:
    value = getattr(plant, key)
    if isinstance(wanted, str):
        return value == wanted
    return value in set(wanted)


def query_plants(
    plant_ids: Sequence[str] | None = None,
    rows: Sequence[Plant] | None = None,
    **filters: str | Sequence[str] | None,
) -> list[Plant]:
    out = list(rows if rows is not None else plants())

    if plant_ids:
        wanted = set(plant_ids)
        out = [p for p in out if p.plant_id in wanted]

    for key, value in filters.items():
        if value is None:
            continue
        if key not in FILTERABLE:
            raise ValueError(f"'{key}' is not a filterable column; expected one of {FILTERABLE}")
        out = [p for p in out if _matches(p, key, value)]

    return sorted(out, key=lambda p: p.plant_id)


def _row(plant: Plant) -> dict:
    return {
        "plant_id": plant.plant_id,
        "plant_name": plant.plant_name,
        "company": plant.company,
        "city": plant.city,
        "country": plant.country,
        "region": plant.region,
        "product": plant.product,
        "capacity_kta": plant.capacity_kta,
        "capacity_status": ("public" if plant.capacity_is_public else "not_publicly_confirmed"),
        "process_route": plant.process_route,
        "startup_year": plant.startup_year,
        "status": plant.status,
        "complex_id": plant.complex_id,
        "source_type": plant.source_type,
        "citation": f"plant:{plant.plant_id}",
    }


def list_plants(**kwargs) -> dict:
    matched = query_plants(**kwargs)
    return {
        "kind": "plant_rows",
        "n": len(matched),
        "rows": [_row(p) for p in matched],
        "citations": [f"plant:{p.plant_id}" for p in matched],
    }


def sum_capacity(
    status: str | Sequence[str] | None = None,
    plant_ids: Sequence[str] | None = None,
    rows: Sequence[Plant] | None = None,
    **filters: str | Sequence[str] | None,
) -> dict:
    """Total capacity, with an explicit account of what it does and does not cover.

    Refuses without a status filter -- see module docstring.
    """
    if status is None:
        return {
            "kind": "error",
            "error": "aggregate requires an explicit status filter",
            "detail": (
                "The register contains Operating, Under construction and Idled units. "
                "Summing across statuses conflates installed capacity with announced "
                "capacity. Pass status=['Operating'] for the operating base, or call once "
                "per status to report them separately."
            ),
            "available_status": ["Operating", "Under construction", "Idled"],
        }

    all_rows = list(rows if rows is not None else plants())
    matched_ignoring_status = query_plants(plant_ids=plant_ids, rows=all_rows, **filters)
    matched = query_plants(plant_ids=plant_ids, rows=all_rows, status=status, **filters)

    included = [p for p in matched if p.capacity_is_public]
    withheld = [p for p in matched if not p.capacity_is_public]
    excluded_by_status = [p for p in matched_ignoring_status if p not in matched]

    total = sum(p.capacity_kta for p in included if p.capacity_kta is not None)

    caveat = (
        f"Total covers {len(included)} of {len(matched)} matched units."
        if not withheld
        else (
            f"Total covers {len(included)} of {len(matched)} matched units. "
            f"{len(withheld)} unit(s) have no publicly confirmed capacity and are excluded "
            "from the total, not counted as zero."
        )
    )

    log.info(
        "plants.aggregate",
        filters=filters,
        status=status,
        n_matched=len(matched),
        n_included=len(included),
        withheld=[p.plant_id for p in withheld],
    )

    return {
        "kind": "plant_aggregate",
        "filters": {**{k: v for k, v in filters.items() if v is not None}, "status": status},
        "n_matched": len(matched),
        "n_included": len(included),
        "sum_capacity_kta": total,
        "unit": "kta",
        "not_publicly_confirmed": [
            {
                "plant_id": p.plant_id,
                "plant_name": p.plant_name,
                "company": p.company,
                "reason": "capacity_kta is blank; source_type='Not publicly confirmed'",
                "citation": f"plant:{p.plant_id}",
            }
            for p in withheld
        ],
        "excluded_by_status": [
            {"plant_id": p.plant_id, "plant_name": p.plant_name, "status": p.status}
            for p in excluded_by_status
        ],
        "citations": [f"plant:{p.plant_id}" for p in included],
        "caveat": caveat,
    }


def describe_register(rows: Sequence[Plant] | None = None) -> dict:
    """Enum values and counts, so the agent filters with real values rather than guesses."""
    all_rows = list(rows if rows is not None else plants())

    def counts(key: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for plant in all_rows:
            out[getattr(plant, key)] = out.get(getattr(plant, key), 0) + 1
        return dict(sorted(out.items()))

    return {
        "kind": "register_description",
        "n_plants": len(all_rows),
        "filterable_columns": list(FILTERABLE),
        "values": {key: counts(key) for key in FILTERABLE},
        "capacity_not_publicly_confirmed": [
            {"plant_id": p.plant_id, "plant_name": p.plant_name, "company": p.company}
            for p in all_rows
            if not p.capacity_is_public
        ],
    }
