"""Resolving a citation token back to the thing it points at.
"""

from __future__ import annotations
from ..config import get_settings
from ..logging import get_logger
from .schemas import SourceResponse
from ingestion.part_a_docs import load_documents
from ingestion.supersession import load_supersession

log = get_logger(__name__)


def _plant(token: str, rest: str) -> SourceResponse:
    from ..services.datasets import plants

    plant = next((p for p in plants() if p.plant_id == rest), None)
    if plant is None:
        return SourceResponse(token=token, kind="plant", resolved=False,
                              error=f"no plant with id '{rest}'")

    capacity = (
        f"{plant.capacity_kta:,.0f} kta"
        if plant.capacity_kta is not None
        else "Not publicly confirmed"
    )
    return SourceResponse(
        token=token,
        kind="plant",
        resolved=True,
        title=f"{plant.plant_id} — {plant.plant_name}",
        subtitle=f"{plant.company} · {plant.city}, {plant.country}",
        fields={
            "product": plant.product,
            "capacity_kta": capacity,
            "process_route": plant.process_route,
            "status": plant.status,
            "startup_year": plant.startup_year,
            "complex_id": plant.complex_id,
            "source_type": plant.source_type,
        },
    )


def _price(token: str, rest: str) -> SourceResponse:
    from ..services.datasets import prices

    parts = rest.split("|")
    if len(parts) != 4:
        return SourceResponse(
            token=token, kind="price", resolved=False,
            error="expected product|region|month|basis",
        )
    product, region, month, basis = parts
    row = next(
        (
            r for r in prices()
            if r.product == product and r.region == region
            and r.month == month and r.basis == basis
        ),
        None,
    )
    if row is None:
        return SourceResponse(
            token=token, kind="price", resolved=False,
            error=f"no assessment for {product} {region} {month} on a {basis} basis",
        )

    value = f"{row.price:,.0f} {row.unit}" if row.price is not None else "Not publicly confirmed"
    return SourceResponse(
        token=token,
        kind="price",
        resolved=True,
        title=f"{product} — {value}",
        subtitle=f"{region} · {month} · {basis} basis",
        fields={
            "month": row.month,
            "product": row.product,
            "region": row.region,
            "price": value,
            "basis": row.basis,
            "source_type": row.source_type,
        },
    )


def _document(token: str, rest: str) -> SourceResponse:

    doc = next((d for d in load_documents() if d.filename == rest), None)
    if doc is None:
        return SourceResponse(token=token, kind="document", resolved=False,
                              error=f"no document named '{rest}'")

    supersession = load_supersession()
    fields = {
        "date": doc.date,
        "source_type": doc.source_type,
        "attribution": doc.attribution,
        "independent": doc.publisher is not None,
    }
    superseded = supersession.superseding(doc.filename)
    if superseded:
        fields["superseded_on"] = ", ".join(
            f"{f} (was {e.superseded_values.get(f, '?')})"
            for e in superseded
            for f in e.scope
        )
    revises = supersession.note_for(doc.filename)
    if revises:
        fields["revises"] = revises
    limits = [n.sentence for n in doc.negative_statements if n.kind == "scope_limitation"]
    if limits:
        fields["scope_limitation"] = " ".join(limits)

    return SourceResponse(
        token=token, kind="document", resolved=True,
        title=doc.title, subtitle=f"{doc.attribution} · {doc.date}",
        fields=fields, text=doc.body,
    )


def _filing(token: str, rest: str) -> SourceResponse:
    from ..services.retrieval import part_b_index

    parts = rest.split("|")
    if len(parts) < 2:
        return SourceResponse(token=token, kind="filing", resolved=False,
                              error="expected company|filename[|p.N]")
    company, filename = parts[0], parts[1]
    page = None
    sheet = None
    cell_range = parts[3] if len(parts) > 3 else None
    if len(parts) > 2:
        if parts[2].startswith("p."):
            try:
                page = int(parts[2][2:])
            except ValueError:
                page = None
        else:
            # A workbook citation names the sheet rather than a page.
            sheet = parts[2]

    filings, companies, chunks, _ = part_b_index()
    filing = next((f for f in filings if f.filename == filename), None)
    if filing is None:
        return SourceResponse(token=token, kind="filing", resolved=False,
                              error=f"no filing named '{filename}'")

    text = None
    if sheet is not None:
        on_sheet = [
            c for c in chunks
            if c.filename == filename and c.sheet == sheet
            and (cell_range is None or c.cell_range == cell_range)
        ]
        if not on_sheet:
            known = sorted({c.sheet for c in chunks if c.filename == filename and c.sheet})
            return SourceResponse(
                token=token, kind="filing", resolved=False,
                error=f"'{filename}' has no sheet '{sheet}'"
                      + (f" with range {cell_range}" if cell_range else ""),
                fields={"available_sheets": known},
            )
        text = "\n\n".join(c.text for c in on_sheet) or None
    elif page is not None:
        on_page = [c for c in chunks if c.filename == filename and c.page == page]
        text = "\n\n".join(c.text for c in on_page) or None

    basis = filing.basis
    fields = {
        "legal_entity": basis.legal_entity,
        "reporting_currency": basis.reporting_currency,
        "units": basis.units,
        "fiscal_year_end": basis.fiscal_year_end_convention,
        "doc_type": filing.doc_type,
    }
    if sheet is not None:
        fields["sheet"] = sheet
        if cell_range:
            fields["cell_range"] = cell_range
        if basis.periods_covered:
            fields["period"] = basis.periods_covered[0]
    else:
        fields["page"] = page
    if basis.adjusted_measures:
        fields["adjusted_measures"] = " · ".join(basis.adjusted_measures)
    if basis.warnings:
        fields["warning"] = " ".join(basis.warnings)

    if sheet is not None:
        locator = f" — sheet {sheet}" + (f" {cell_range}" if cell_range else "")
    else:
        locator = f" — page {page}" if page else ""

    return SourceResponse(
        token=token, kind="filing", resolved=True,
        title=f"{filename}{locator}",
        subtitle=f"{basis.legal_entity} · {company}",
        fields=fields, text=text,
    )


def resolve(token: str) -> SourceResponse:
    prefix, _, rest = token.partition(":")
    handlers = {
        "plant": _plant,
        "price": _price,
        "doc": _document,
        "filing": _filing,
    }
    handler = handlers.get(prefix)
    if handler is None:
        return SourceResponse(
            token=token, kind=prefix or "unknown", resolved=False,
            error=f"unknown citation kind '{prefix}'",
        )
    try:
        return handler(token, rest)
    except Exception as exc:  # noqa: BLE001
        log.warning("sources.resolve_failed", token=token, error=str(exc)[:200])
        return SourceResponse(token=token, kind=prefix, resolved=False, error=str(exc)[:200])


def documents_dir_exists() -> bool:
    return get_settings().documents_dir.exists()
