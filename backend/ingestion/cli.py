"""Ingestion CLI.

    python -m ingestion.cli validate     shape checks only, no writes
    python -m ingestion.cli manifest     write var/index/manifest.json
    python -m ingestion.cli describe     print what the agent will see as tool metadata
    python -m ingestion.cli all          validate, then manifest

Ingestion is a build-time step. Nothing here runs while a note is being written.
"""

from __future__ import annotations

import argparse
import json
import sys

from app.config import get_settings
from app.logging import configure_logging, get_logger

log = get_logger("ingestion.cli")


def cmd_validate() -> int:
    problems = __import__("ingestion.manifest", fromlist=["validate"]).validate()
    if problems:
        print(f"FAILED: {len(problems)} problem(s)\n")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("OK: corpus matches the expected shape")
    print("  52 plants, 190 price rows, 27 documents")
    print("  7 price series including the Propylene Spot->Contract break at 2026-01")
    print("  PL-008 capacity blank; PP 2026-07/08 prices blank")
    return 0


def cmd_manifest() -> int:
    from ingestion import manifest as m

    problems = m.validate()
    if problems:
        print("refusing to write a manifest for a corpus that failed validation:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    built = m.build()
    path = m.write(built)
    print(f"wrote {path}")
    print(f"  fingerprint : {built.corpus_fingerprint}")
    print(f"  part A files: {len(built.part_a)}")
    print(f"  part B files: {len(built.part_b)}")
    return 0


def cmd_embed() -> int:
    """Embed Part B prose chunks into Chroma. Requires Azure credentials.

    Tables are not embedded: a vector for a grid of numbers carries almost no
    semantic signal, and they are reached lexically by their row labels instead.
    """
    import asyncio

    from app.services.embeddings import get_embedder
    from app.services.retrieval import part_b_index
    from app.services.vectorstore import open_index
    from ingestion.manifest import CHUNKER_VERSION

    embedder = get_embedder()
    if embedder is None:
        print("no embedder available (PROVIDER_MODE is not 'live', or credentials are missing).")
        print("Retrieval will run lexical-only, which is a supported mode -- the tool")
        print("reports retrieval_mode='lexical_only' so a note knows recall was reduced.")
        return 1

    _, _, chunks, _ = part_b_index()
    prose = [c for c in chunks if c.embeddable]
    print(f"embedding {len(prose)} prose chunks of {len(chunks)} total "
          f"({len(chunks) - len(prose)} tables are lexical-only by design)")

    index = open_index(embedder.model, CHUNKER_VERSION)
    if index is None:
        print("could not open the vector store")
        return 1

    texts = [f"{c.heading or ''}\n{c.text}".strip() for c in prose]
    vectors = asyncio.run(embedder.embed(texts))
    index.upsert(
        ids=[c.chunk_id for c in prose],
        vectors=vectors,
        documents=texts,
        metadatas=[
            {
                "company_code": c.company_code,
                "filename": c.filename,
                "doc_type": c.doc_type,
                "page": c.page,
                "kind": c.kind.value,
            }
            for c in prose
        ],
    )
    print(f"done: {index.count} vectors in the collection")
    return 0


def cmd_describe() -> int:
    from app.services.plant_query import describe_register
    from app.services.price_query import available_regions
    from app.services.datasets import prices

    register = describe_register()
    series: dict[str, list[str]] = {}
    for row in prices():
        series.setdefault(f"{row.product}|{row.region}|{row.basis}", []).append(row.month)

    print(json.dumps({
        "register": {
            "n_plants": register["n_plants"],
            "values": register["values"],
            "capacity_not_publicly_confirmed": register["capacity_not_publicly_confirmed"],
        },
        "price_series": {
            key: {"n": len(months), "from": min(months), "to": max(months)}
            for key, months in sorted(series.items())
        },
        "regions_by_product": {
            product: available_regions(product)
            for product in sorted({r.product for r in prices()})
        },
    }, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ingestion", description="Build-time ingestion")
    parser.add_argument(
        "command", choices=["validate", "manifest", "describe", "embed", "all"]
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    if args.command == "validate":
        return cmd_validate()
    if args.command == "manifest":
        return cmd_manifest()
    if args.command == "describe":
        return cmd_describe()
    if args.command == "embed":
        return cmd_embed()
    return cmd_validate() or cmd_manifest()


if __name__ == "__main__":
    sys.exit(main())
