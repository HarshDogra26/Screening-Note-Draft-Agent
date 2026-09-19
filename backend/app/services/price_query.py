"""The price series engine.
"""

from __future__ import annotations
from collections.abc import Iterable, Sequence
from ..domain.continuity import (
    BasisSegment,
    ComparabilityWarning,
    PricePoint,
    SeriesContinuity,
    SeriesResult,
)
from ..domain.enums import ComparabilityVerdict, WarningCode
from ..logging import get_logger
from .datasets import PriceRow, prices

log = get_logger(__name__)

CORROBORATING_DOCS: dict[tuple[str, str, WarningCode], str] = {
    (
        "Propylene",
        "Southeast Asia CFR",
        WarningCode.BASIS_CHANGE,
    ): "025_asean_monitor_price_commentary_2026-06-18.md",
}


# --- month arithmetic ------------------------------------------------------


def _to_ord(month: str) -> int:
    year, mon = month.split("-")
    return int(year) * 12 + (int(mon) - 1)


def _from_ord(value: int) -> str:
    return f"{value // 12:04d}-{value % 12 + 1:02d}"


def month_range(first: str, last: str) -> list[str]:
    if _to_ord(first) > _to_ord(last):
        return []
    return [_from_ord(o) for o in range(_to_ord(first), _to_ord(last) + 1)]


# --- selection -------------------------------------------------------------


def available_regions(product: str, rows: Iterable[PriceRow] | None = None) -> list[str]:
    rows = rows if rows is not None else prices()
    return sorted({r.region for r in rows if r.product == product})


def normalise_region(product: str, region: str, rows: Sequence[PriceRow] | None = None) -> str | None:
    """Resolve a region name to an assessment location, if it does so unambiguously.
    """
    available = available_regions(product, rows)
    if region in available:
        return region
    wanted = region.strip().casefold()
    matches = [
        r for r in available
        if r.casefold() == wanted or r.casefold().removesuffix(" cfr") == wanted
    ]
    return matches[0] if len(matches) == 1 else None


def select(
    product: str,
    region: str | None = None,
    basis: str | None = None,
    rows: Sequence[PriceRow] | None = None,
) -> list[PriceRow]:
    rows = rows if rows is not None else prices()
    out = [r for r in rows if r.product == product]
    if region is not None:
        out = [r for r in out if r.region == region]
    if basis is not None:
        out = [r for r in out if r.basis == basis]
    return sorted(out, key=lambda r: (r.month, r.basis))


# --- continuity ------------------------------------------------------------


def _segments(points: Sequence[PricePoint]) -> list[BasisSegment]:
    """Contiguous runs of identical basis, in month order."""
    segments: list[BasisSegment] = []
    run: list[PricePoint] = []

    def flush() -> None:
        if not run:
            return
        values = [p.price for p in run if p.price is not None]
        segments.append(
            BasisSegment(
                basis=run[0].basis,
                month_from=run[0].month,
                month_to=run[-1].month,
                n=len(run),
                first=values[0] if values else None,
                last=values[-1] if values else None,
                min=min(values) if values else None,
                max=max(values) if values else None,
                mean=round(sum(values) / len(values), 2) if values else None,
            )
        )

    for point in points:
        if run and point.basis != run[-1].basis:
            flush()
            run = []
        run.append(point)
    flush()
    return segments


def query_series(
    product: str,
    region: str | None = None,
    month_from: str | None = None,
    month_to: str | None = None,
    basis: str | None = None,
    compute: str = "none",
    rows: Sequence[PriceRow] | None = None,
) -> SeriesResult | dict:
    """Return a series with its continuity and comparability verdict.
    """
    regions = available_regions(product, rows)
    if not regions:
        return {
            "kind": "error",
            "error": f"'{product}' is not an assessed product",
            "available_products": sorted({r.product for r in (rows if rows is not None else prices())}),
        }

    requested_region = region
    if region is None:
        if len(regions) != 1:
            return {
                "kind": "error",
                "error": f"'{product}' is assessed in more than one location; specify region.",
                "available_regions": regions,
            }
        region = regions[0]
    else:
        resolved = normalise_region(product, region, rows)
        if resolved is None:
            return {
                "kind": "error",
                "error": f"'{region}' is not an assessment location for {product}",
                "available_regions": regions,
                "hint": (
                    "Assessment locations are not the same vocabulary as the plant "
                    "register's regions: a plant is in 'Southeast Asia', a price is "
                    "assessed at 'Southeast Asia CFR'. Use one of the values above "
                    "exactly."
                ),
            }
        region = resolved

    selected = select(product, region, basis, rows)
    if not selected:
        return {
            "kind": "error",
            "error": (
                f"no assessments for product='{product}' region='{region}'"
                + (f" basis='{basis}'" if basis else "")
            ),
            "available_regions": regions,
            "available_bases": sorted({r.basis for r in select(product, region, rows=rows)}),
        }

    covered = [r.month for r in selected]
    first = month_from or min(covered)
    last = month_to or max(covered)

    by_month: dict[str, PriceRow] = {r.month: r for r in selected if first <= r.month <= last}

    points: list[PricePoint] = []
    missing: list[str] = []
    unconfirmed: list[str] = []

    for month in month_range(first, last):
        row = by_month.get(month)
        if row is None:
            missing.append(month)
            continue
        if row.price is None:
            unconfirmed.append(month)
        points.append(
            PricePoint(
                month=row.month,
                product=row.product,
                region=row.region,
                basis=row.basis,
                price=row.price,
                unit=row.unit,
                source_type=row.source_type,
            )
        )

    segments = _segments(points)
    warnings: list[ComparabilityWarning] = []
    verdict = ComparabilityVerdict.COMPARABLE

    if len(segments) > 1:
        verdict = ComparabilityVerdict.NOT_COMPARABLE
        spans = ", ".join(f"{s.basis} ({s.month_from}..{s.month_to})" for s in segments)
        warnings.append(
            ComparabilityWarning(
                code=WarningCode.BASIS_CHANGE,
                detail=(
                    f"Series changes basis: {spans}. Values assessed on different bases are "
                    "not directly comparable, and the step between segments reflects the "
                    "change of basis rather than a market movement."
                ),
                corroborating_document=CORROBORATING_DOCS.get(
                    (product, region, WarningCode.BASIS_CHANGE)
                ),
            )
        )

    if missing:
        if verdict is ComparabilityVerdict.COMPARABLE:
            verdict = ComparabilityVerdict.COMPARABLE_WITH_CAVEAT
        warnings.append(
            ComparabilityWarning(
                code=WarningCode.COVERAGE_GAP,
                detail=(
                    f"No assessment published for {', '.join(missing)}. A missing month does "
                    "not mean the price was zero or unchanged; these months are not "
                    "interpolated."
                ),
            )
        )

    if unconfirmed:
        if verdict is ComparabilityVerdict.COMPARABLE:
            verdict = ComparabilityVerdict.COMPARABLE_WITH_CAVEAT
        warnings.append(
            ComparabilityWarning(
                code=WarningCode.ENDPOINT_NOT_PUBLICLY_CONFIRMED,
                detail=(
                    f"Assessment rows exist but values are not publicly confirmed for "
                    f"{', '.join(unconfirmed)}. Statistics over this range cover "
                    f"{len(points) - len(unconfirmed)} of {len(points)} months."
                ),
            )
        )

    if requested_region is not None and requested_region != region:
        warnings.append(
            ComparabilityWarning(
                code=WarningCode.REGION_MISMATCH,
                detail=(
                    f"Requested region '{requested_region}' was read as the assessment "
                    f"location '{region}', the only one matching it for {product}."
                ),
            )
        )

    result = SeriesResult(
        product=product,
        region=region,
        points=points,
        continuity=SeriesContinuity(
            requested_from=first,
            requested_to=last,
            months_requested=len(month_range(first, last)),
            months_present=len(points),
            missing_months=tuple(missing),
            unconfirmed_months=tuple(unconfirmed),
            basis_segment_count=len(segments),
        ),
        verdict=verdict,
        comparability_warnings=warnings,
        basis_segments=segments,
    )

    if compute == "change":
        result = _apply_change(result)

    log.info(
        "price.query",
        product=product,
        region=region,
        months=len(points),
        verdict=verdict.value,
        segments=len(segments),
        missing=len(missing),
        unconfirmed=len(unconfirmed),
    )
    return result


def _apply_change(result: SeriesResult) -> SeriesResult:
    """Attach a change, or refuse with a reason. Never guesses past a null endpoint."""
    if result.verdict is ComparabilityVerdict.NOT_COMPARABLE:
        return result.model_copy(
            update={
                "change": None,
                "change_refused_reason": (
                    "Cannot compute a change across a basis break. Per-segment statistics "
                    "are given in basis_segments instead."
                ),
            }
        )

    if not result.points:
        return result.model_copy(
            update={"change_refused_reason": "No assessments in the requested range."}
        )

    start, end = result.points[0], result.points[-1]
    if start.price is None or end.price is None:
        which = "start" if start.price is None else "end"
        month = start.month if start.price is None else end.month
        return result.model_copy(
            update={
                "change": None,
                "change_refused_reason": (
                    f"The {which} of the requested range ({month}) is not publicly confirmed, "
                    "so a change cannot be computed. Use the last confirmed month instead and "
                    "label it as such."
                ),
            }
        )

    change = round(end.price - start.price, 2)
    return result.model_copy(
        update={
            "change": change,
            "change_pct": round(change / start.price * 100, 2) if start.price else None,
        }
    )


def query_spread(
    product_a: str,
    product_b: str,
    region_a: str | None = None,
    region_b: str | None = None,
    month_from: str | None = None,
    month_to: str | None = None,
    rows: Sequence[PriceRow] | None = None,
) -> dict:
    """Spread between two series, e.g. a naphtha/ethylene crack proxy.
    """
    leg_a = query_series(product_a, region_a, month_from, month_to, rows=rows)
    leg_b = query_series(product_b, region_b, month_from, month_to, rows=rows)
    for leg in (leg_a, leg_b):
        if isinstance(leg, dict):
            return leg

    warnings = list(leg_a.comparability_warnings) + list(leg_b.comparability_warnings)
    if leg_a.region != leg_b.region:
        warnings.append(
            ComparabilityWarning(
                code=WarningCode.REGION_MISMATCH,
                detail=(
                    f"{product_a} is assessed '{leg_a.region}'; {product_b} is assessed "
                    f"'{leg_b.region}'. These are different assessment locations, so the "
                    "spread joins series on different geographies."
                ),
            )
        )

    b_by_month = {p.month: p for p in leg_b.points}
    series: list[dict] = []
    for point in leg_a.points:
        other = b_by_month.get(point.month)
        if other is None or point.price is None or other.price is None:
            continue
        series.append(
            {
                "month": point.month,
                "spread": round(other.price - point.price, 2),
                "citations": [point.citation_token, other.citation_token],
            }
        )

    return {
        "kind": "price_spread",
        "minuend": {"product": product_b, "region": leg_b.region},
        "subtrahend": {"product": product_a, "region": leg_a.region},
        "unit": "USD/tonne",
        "formula": f"{product_b} - {product_a}",
        "months": series,
        "comparability_warnings": [w.model_dump() for w in warnings],
        "region_mismatch": leg_a.region != leg_b.region,
    }
