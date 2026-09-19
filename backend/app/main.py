"""FastAPI application.
"""

from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .api.routes import router
from .config import get_settings
from .logging import configure_logging, get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    # Say plainly at startup which mode this process is in, rather than letting a
    # missing credential surface later as quietly reduced retrieval.
    log.info(
        "app.start",
        provider_mode=settings.provider_mode.value,
        mcp_url=settings.mcp_url,
        mcp_inprocess=settings.mcp_inprocess,
        missing_credentials=settings.missing_live_credentials(),
    )
    if not settings.is_live:
        log.warning(
            "app.fixture_mode",
            effect=(
                "LLM calls replay from cassettes and retrieval is lexical-only. "
                "Set PROVIDER_MODE=live with Azure credentials for real runs."
            ),
        )
    yield
    log.info("app.stop")


app = FastAPI(
    title="Screening Note Agent",
    version="0.1.0",
    description=(
        "Turns a one-line brief into a structured, claim-level cited screening note "
        "over a petrochemical plant register, price series, document corpus and "
        "company filings."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "screening-note-agent", "docs": "/docs", "health": "/api/health"}
