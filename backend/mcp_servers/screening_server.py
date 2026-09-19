"""MCP server exposing the screening agent's tools.
"""

from __future__ import annotations
import argparse
import sys
from pathlib import Path
from typing import Annotated, Any
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastmcp import FastMCP  # noqa: E402
from pydantic import Field  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.logging import configure_logging, get_logger  # noqa: E402
from app.services import plant_query as pq  # noqa: E402
from app.services import price_query as prq  # noqa: E402
from app.services.retrieval import part_a_index, part_b_index, search_part_b  # noqa: E402

log = get_logger("mcp.screening")

mcp = FastMCP(
    name="screening",
    instructions=(
        "Tools over a petrochemical plant register, a monthly price assessment series, "
        "a synthetic document corpus (Part A) and three real companies' filings "
        "(Part B). Part A and Part B must never appear in the same note. "
        "Where a figure is not publicly confirmed, say so -- never estimate. Where two "
        "sources disagree, report both with attribution -- never pick one silently."
    ),
)


# --- 1. corpus description -------------------------------------------------


@mcp.tool(
    name="describe_corpus",
    description=(
        "Call this FIRST, before any other tool. Returns the exact values you may "
        "filter on (companies, countries, regions, products, statuses, process "
        "routes), the full price series inventory with each series' coverage and "
        "gaps, the Part A document list, and the Part B filing list with each "
        "company's reporting currency and fiscal year end. Using a value that is not "
        "in this response will return no rows."
    ),
)
async def describe_corpus(
    section: Annotated[
        str,
        Field(description="Which part to describe: 'register', 'prices', 'documents', 'filings' or 'all'."),
    ] = "all",
) -> dict[str, Any]:
    from app.services.datasets import prices

    out: dict[str, Any] = {"kind": "corpus_description"}

    if section in ("all", "register"):
        out["register"] = pq.describe_register()

    if section in ("all", "prices"):
        series: dict[str, list[str]] = {}
        blanks: dict[str, list[str]] = {}
        for row in prices():
            key = f"{row.product}|{row.region}|{row.basis}"
            series.setdefault(key, []).append(row.month)
            if row.price is None:
                blanks.setdefault(key, []).append(row.month)
        inventory = {}
        for key, months in sorted(series.items()):
            span = prq.month_range(min(months), max(months))
            inventory[key] = {
                "n_rows": len(months),
                "from": min(months),
                "to": max(months),
                "months_with_no_row": sorted(set(span) - set(months)),
                "months_with_blank_price": sorted(blanks.get(key, [])),
            }
        out["price_series"] = inventory
        out["price_series_note"] = (
            "A product assessed on two bases appears as two series. Values on "
            "different bases are not directly comparable."
        )

    if section in ("all", "documents"):
        docs, _ = part_a_index()
        out["documents"] = [
            {
                "filename": d.filename,
                "date": d.date,
                "title": d.title,
                "source_type": d.source_type,
                "attribution": d.attribution,
                "scope_limitations": [
                    n.sentence for n in d.negative_statements if n.kind == "scope_limitation"
                ],
            }
            for d in docs
        ]

    if section in ("all", "filings"):
        _, companies, _chunks, _ = part_b_index()
        out["filings"] = {
            code: {
                "legal_entity": cb.legal_entity,
                "reporting_currency": cb.reporting_currency,
                "units": cb.units,
                "fiscal_year_end": cb.fiscal_year_end_convention,
                "filings": list(cb.filings),
                "adjusted_measure_definitions": list(cb.adjusted_measures),
            }
            for code, cb in companies.items()
        }
        out["filings_note"] = (
            "Company codes are read from the documents, not from folder names. There "
            "is no PETRONAS Chemicals Group (PCG) filing in this corpus: the folder so "
            "named contains filings of Petroliam Nasional Berhad, the integrated parent."
        )

    return out


# --- 2. plant register -----------------------------------------------------


@mcp.tool(
    name="query_plant_register",
    description=(
        "Query the register of 52 petrochemical plants. Filter by company, country, "
        "region, product, status, process_route or complex_id, or fetch specific "
        "plant_ids. Set aggregate=true for a capacity total.\n\n"
        "AGGREGATION REQUIRES an explicit status filter: the register mixes Operating, "
        "Under construction and Idled units and summing them conflates installed with "
        "announced capacity.\n\n"
        "Some plants have no publicly confirmed capacity. They are returned in "
        "not_publicly_confirmed and are excluded from totals -- they are NEVER counted "
        "as zero, and you must not estimate them. Cite every fact by plant_id."
    ),
)
async def query_plant_register(
    plant_ids: Annotated[list[str] | None, Field(description="Specific ids, e.g. ['PL-001'].")] = None,
    company: Annotated[str | None, Field(description="Exact company name.")] = None,
    country: Annotated[str | None, Field(description="Exact country name.")] = None,
    region: Annotated[str | None, Field(description="Exact region, e.g. 'Southeast Asia'.")] = None,
    product: Annotated[str | None, Field(description="Ethylene, Propylene, HDPE, LDPE, LLDPE or PP.")] = None,
    status: Annotated[list[str] | None, Field(description="Operating, Under construction, Idled.")] = None,
    process_route: Annotated[str | None, Field(description="Exact process route.")] = None,
    complex_id: Annotated[str | None, Field(description="Units sharing a site, e.g. 'CPX-ID-01'.")] = None,
    aggregate: Annotated[bool, Field(description="Return a capacity total instead of rows.")] = False,
) -> dict[str, Any]:
    filters = {
        "company": company,
        "country": country,
        "region": region,
        "product": product,
        "process_route": process_route,
        "complex_id": complex_id,
    }
    try:
        if aggregate:
            return pq.sum_capacity(status=status, plant_ids=plant_ids, **filters)
        return pq.list_plants(plant_ids=plant_ids, status=status, **filters)
    except ValueError as exc:
        return {"kind": "error", "tool": "query_plant_register", "error": str(exc)}


# --- 3. price series -------------------------------------------------------


@mcp.tool(
    name="query_price_series",
    description=(
        "Query monthly price assessments for Naphtha, Ethylene, Propylene, HDPE or PP.\n\n"
        "Every response carries a continuity block distinguishing two different things: "
        "months with NO ROW (no assessment was published) and months whose row exists "
        "with a BLANK PRICE (not publicly confirmed). Neither may be interpolated or "
        "carried forward.\n\n"
        "If the requested range crosses a change of assessment basis (Spot to Contract), "
        "this tool REFUSES to compute a change and returns per-basis segment statistics "
        "instead. The step at such a break reflects the methodology change, not a market "
        "move. Use compute='spread' to compare two products; it will tell you if the two "
        "legs are assessed in different locations. Cite each value as "
        "product|region|month|basis."
    ),
)
async def query_price_series(
    product: Annotated[str, Field(description="Naphtha, Ethylene, Propylene, HDPE or PP.")],
    region: Annotated[str | None, Field(description="'Southeast Asia CFR', 'Northeast Asia CFR' or 'Asia CFR'. Omit only if the product has one location.")] = None,
    month_from: Annotated[str | None, Field(description="Inclusive start, YYYY-MM.")] = None,
    month_to: Annotated[str | None, Field(description="Inclusive end, YYYY-MM.")] = None,
    basis: Annotated[str | None, Field(description="'Spot' or 'Contract'. Omit to see the basis change if there is one.")] = None,
    compute: Annotated[str, Field(description="'none', 'change' or 'spread'.")] = "none",
    spread_against: Annotated[str | None, Field(description="Second product for compute='spread', e.g. 'Ethylene'.")] = None,
    spread_against_region: Annotated[str | None, Field(description="Region for the second leg.")] = None,
) -> dict[str, Any]:
    if compute == "spread":
        if not spread_against:
            return {
                "kind": "error",
                "error": "compute='spread' requires spread_against",
                "hint": "e.g. product='Naphtha', spread_against='Ethylene'",
            }
        return prq.query_spread(
            product, spread_against, region, spread_against_region, month_from, month_to
        )

    result = prq.query_series(product, region, month_from, month_to, basis, compute)
    if isinstance(result, dict):
        return {"kind": "error", **result} if result.get("kind") != "error" else result
    return result.model_dump(mode="json")


# --- 4. Part A documents ---------------------------------------------------


@mcp.tool(
    name="search_part_a_documents",
    description=(
        "Search 27 short documents: company press releases, industry association "
        "bulletins and government agency notes, dated September 2025 to August 2026. "
        "Returns FULL document text -- these are short and are not chunked.\n\n"
        "Use this whenever you need CAUSATION or CONTEXT: the price series shows what "
        "moved, only these documents say why. Also use it to check whether a figure in "
        "the register is disputed by another source.\n\n"
        "Each result reports who is speaking (a company about itself, or an independent "
        "body), whether the document states a limit on what its publisher covers, and "
        "whether it has been superseded on any specific point. Cite by filename."
    ),
)
async def search_part_a_documents(
    query: Annotated[str, Field(description="What you are looking for.", min_length=2)],
    k: Annotated[int, Field(description="How many documents.", ge=1, le=27)] = 5,
    company: Annotated[str | None, Field(description="Restrict to one company's releases.")] = None,
    source_type: Annotated[str | None, Field(description="'Company press release', 'Industry association bulletin' or 'Government agency publication'.")] = None,
    date_from: Annotated[str | None, Field(description="Inclusive, YYYY-MM-DD.")] = None,
    date_to: Annotated[str | None, Field(description="Inclusive, YYYY-MM-DD.")] = None,
) -> dict[str, Any]:
    from ingestion.supersession import load_supersession

    docs, index = part_a_index()
    supersession = load_supersession()

    known_companies = sorted({d.company for d in docs if d.company})
    if company and company not in known_companies:
        return {
            "kind": "error",
            "error": f"no documents from company '{company}'",
            "available_companies": known_companies,
        }
    known_source_types = sorted({d.source_type for d in docs})
    if source_type and source_type not in known_source_types:
        return {
            "kind": "error",
            "error": f"'{source_type}' is not a document source type",
            "available_source_types": known_source_types,
        }

    def keep(doc) -> bool:
        if company and doc.company != company:
            return False
        if source_type and doc.source_type != source_type:
            return False
        if date_from and doc.date < date_from:
            return False
        return not (date_to and doc.date > date_to)

    hits = index.search(query, k=k, predicate=keep)

    results = []
    for doc, score in hits:
        superseded_on = [
            {
                "by": edge.superseded_by,
                "fields": list(edge.scope),
                "was": edge.superseded_values,
                "now": edge.current_values,
                "still_current_here": list(edge.still_current_in_superseded),
            }
            for edge in supersession.superseding(doc.filename)
        ]
        results.append(
            {
                "filename": doc.filename,
                "citation": doc.citation_token,
                "date": doc.date,
                "title": doc.title,
                "source_type": doc.source_type,
                "attribution": doc.attribution,
                "is_independent": doc.publisher is not None,
                "score": round(score, 4),
                "text": doc.body,
                "scope_limitations": [
                    n.sentence for n in doc.negative_statements if n.kind == "scope_limitation"
                ],
                "not_disclosed": [
                    n.sentence for n in doc.negative_statements if n.kind == "not_disclosed"
                ],
                "out_of_scope_statements": [
                    n.sentence for n in doc.negative_statements if n.kind == "out_of_scope"
                ],
                "superseded_on": superseded_on,
                "revises": supersession.note_for(doc.filename),
            }
        )

    payload = {"kind": "part_a_documents", "query": query, "n": len(results),
               "results": results}
    if not results:
        payload["empty_result_note"] = (
            "No documents matched. This does NOT establish that the corpus lacks this "
            "information — try different wording or fewer filters before concluding "
            "anything is absent."
        )
    return payload


# --- 5. Part B filings -----------------------------------------------------


@mcp.tool(
    name="search_part_b_filings",
    description=(
        "Search the real company filings: annual reports, interim reports and results "
        "media releases for three listed groups. Returns page-level extracts.\n\n"
        "EVERY result carries that filing's basis of preparation -- reporting currency, "
        "units, fiscal year end, period covered, and the company's own definition of any "
        "adjusted earnings measure. Read it before comparing anything across companies: "
        "the three report in different currencies, in different units, to different "
        "fiscal year ends, and define adjusted earnings differently.\n\n"
        "Company identity is taken from the documents, not from folder names. Never "
        "convert currencies and never annualise. Cite as company|filename|page."
    ),
)
async def search_part_b_filings(
    query: Annotated[str, Field(description="What you are looking for.", min_length=2)],
    k: Annotated[int, Field(description="How many pages.", ge=1, le=20)] = 5,
    company: Annotated[str | None, Field(description="'PETRONAS', 'GC' or 'RIL'. Call describe_corpus for the list.")] = None,
    doc_type: Annotated[str | None, Field(description="One of 'annual_report', 'interim_report', 'media_release', 'financial_report'. NOT a filename — use `filename` for that.")] = None,
    filename: Annotated[str | None, Field(description="Restrict to one filing by its exact filename.")] = None,
) -> dict[str, Any]:
    filings, companies, _chunks, _index = part_b_index()

    known_types = sorted({f.doc_type for f in filings})
    if doc_type and doc_type not in known_types:
        return {
            "kind": "error",
            "error": f"'{doc_type}' is not a document type",
            "available_doc_types": known_types,
            "hint": (
                "If you meant a specific file, pass it as `filename` instead. "
                "This is a filter, not a search term — it returns nothing if it does "
                "not match exactly."
            ),
        }

    known_files = sorted({f.filename for f in filings})
    if filename and filename not in known_files:
        return {
            "kind": "error",
            "error": f"no filing named '{filename}'",
            "available_filenames": known_files,
        }

    if company and company not in companies:
        return {
            "kind": "error",
            "error": f"no filings for company '{company}'",
            "available": sorted(companies),
            "note": (
                "There is no PCG filing in this corpus. The folder labelled 'PETRONAS "
                "Chemicals Group (PCG)' contains filings of Petroliam Nasional Berhad, "
                "the integrated parent, which is a different entity."
            ),
        }

    by_file = {f.filename: f for f in filings}

    def keep(chunk, code: str | None = None) -> bool:
        target = code or company
        if target and chunk.company_code != target:
            return False
        if doc_type and chunk.doc_type != doc_type:
            return False
        return not (filename and chunk.filename != filename)

    if company:
        hits, mode = await search_part_b(query, k=k, predicate=keep)
    else:
        per_company = max(1, -(-k // max(1, len(companies))))
        pooled: list = []
        modes: set[str] = set()
        for code in sorted(companies):
            got, mode = await search_part_b(
                query, k=per_company, predicate=lambda c, code=code: keep(c, code)
            )
            pooled.extend(got)
            modes.add(mode)
        pooled.sort(key=lambda c: c.chunk_id)
        hits = pooled[:k]
        mode = "hybrid" if modes == {"hybrid"} else sorted(modes)[0]

    results = []
    for chunk in hits:
        basis = by_file[chunk.filename].basis
        results.append(
            {
                "citation": chunk.citation_token,
                "chunk_id": chunk.chunk_id,
                "company_code": chunk.company_code,
                "legal_entity": basis.legal_entity,
                "filename": chunk.filename,
                "doc_type": chunk.doc_type,
                "page": chunk.page if chunk.sheet is None else None,
                "sheet": chunk.sheet,
                "cell_range": chunk.cell_range,
                "content_kind": chunk.kind.value,
                "text": chunk.text,
                "heading": chunk.heading,
                "continues_from_previous_page": chunk.continues_from_previous_page,
                "basis_of_preparation": {
                    "reporting_currency": basis.reporting_currency,
                    "units": basis.units,
                    "fiscal_year_end": basis.fiscal_year_end_convention,
                    "periods_covered": list(basis.periods_covered),
                    "adjusted_measures": list(basis.adjusted_measures),
                },
                "warnings": basis.warnings,
            }
        )

    applied = {k: v for k, v in
               {"company": company, "doc_type": doc_type, "filename": filename}.items() if v}
    payload: dict[str, Any] = {
        "kind": "part_b_filings",
        "query": query,
        "n": len(results),
        "retrieval_mode": mode,
        "filters_applied": applied,
        "results": results,
    }
    if not results:
        payload["empty_result_note"] = (
            "No passages matched. This does NOT establish that the corpus lacks this "
            "information."
            + (
                f" Filters were applied ({applied}); try removing them or widening the "
                "query before concluding anything is absent."
                if applied
                else " Try different wording before concluding anything is absent."
            )
        )
    if mode != "hybrid":
        payload["retrieval_note"] = (
            "Semantic retrieval is unavailable, so this search matched on wording only. "
            "Relevant passages phrased differently from the query may have been missed. "
            "Treat absence of evidence here as inconclusive rather than as absence."
        )

    codes = sorted({r["company_code"] for r in results})
    if len(codes) > 1:
        payload["cross_company_comparability"] = {
            "companies": codes,
            "currencies": {c: companies[c].reporting_currency for c in codes},
            "units": {c: companies[c].units for c in codes},
            "units_by_filing": {
                c: companies[c].units_by_filing
                for c in codes
                if companies[c].units is None
            },
            "fiscal_year_ends": {c: companies[c].fiscal_year_end_convention for c in codes},
            "adjusted_measure_definitions": {
                c: list(companies[c].adjusted_measures) for c in codes
            },
            "warning": (
                "These figures are reported in different currencies and units, to "
                "different fiscal year ends. Do not convert, annualise or compare them "
                "directly; report each on its own basis and state the differences."
            ),
        }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP screening tool server")
    parser.add_argument("--stdio", action="store_true", help="Use stdio transport.")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    if args.stdio:
        mcp.run(transport="stdio")
    else:
        host = args.host or settings.mcp_host
        port = args.port or settings.mcp_port
        log.info("mcp.serving", transport="http", host=host, port=port, path="/mcp")
        mcp.run(transport="http", host=host, port=port, path="/mcp")


if __name__ == "__main__":
    main()
