"""HTTP routes.
"""

from __future__ import annotations
import asyncio
import json
import uuid
from typing import Any
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from ..config import get_settings
from ..graph.builder import stream_brief
from ..graph.tools import session
from ..logging import get_logger
from .schemas import BriefRequest, HealthResponse, RunAccepted, RunStatus, SourceResponse
from .sources import resolve
from .store import store

log = get_logger(__name__)
router = APIRouter(prefix="/api")


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()

    tools: list[str] = []
    reachable = False
    try:
        async with session() as mcp:
            tools = await mcp.list_tools()
            reachable = True
    except Exception as exc:  # noqa: BLE001
        log.warning("health.mcp_unreachable", error=str(exc)[:200])

    import os

    from ..graph.nodes import _manifest
    from ..providers.tracing import tracing_enabled
    from ..services.retrieval import _dense_index

    embedder, index = _dense_index()
    retrieval_mode = "hybrid" if embedder and index else "lexical_only"
    tracing = tracing_enabled()

    return HealthResponse(
        status="ok" if reachable else "degraded",
        provider_mode=settings.provider_mode.value,
        retrieval_mode=retrieval_mode,
        mcp_reachable=reachable,
        tracing=tracing,
        tracing_project=(
            os.getenv("LANGSMITH_PROJECT") or os.getenv("LANGCHAIN_PROJECT")
            if tracing
            else None
        ),
        mcp_tools=tools,
        index_manifest=_manifest(),
        missing_credentials=settings.missing_live_credentials(),
    )


@router.post("/notes", response_model=RunAccepted, status_code=202)
async def create_note(request: BriefRequest) -> RunAccepted:
    run_id = uuid.uuid4().hex[:12]
    run = store.create(run_id, request.brief)

    async def execute() -> None:
        try:
            async for kind, payload in stream_brief(request.brief, run_id=run_id):
                if kind == "note":
                    run.note = payload.get("note")
                    run.dropped_claims = payload.get("dropped_claims", [])
                    run.violations = payload.get("violations", [])
                run.publish(kind, payload)
            run.finish("complete")
        except Exception as exc:  # noqa: BLE001
            log.error("run.failed", run_id=run_id, error=str(exc)[:400])
            run.publish("error", {"error": f"{type(exc).__name__}: {exc}"[:400]})
            run.finish("failed", error=str(exc)[:400])

    asyncio.create_task(execute())
    return RunAccepted(run_id=run_id, stream_url=f"/api/notes/{run_id}/stream")


@router.get("/notes/{run_id}/stream")
async def stream(run_id: str) -> StreamingResponse:
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no run '{run_id}'")

    async def events():
        queue = run.subscribe()
        try:
            while True:
                kind, payload = await queue.get()
                yield f"event: {kind}\ndata: {json.dumps(payload, default=str)}\n\n"
                if kind == "closed":
                    break
        finally:
            run.unsubscribe(queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/notes/{run_id}", response_model=RunStatus)
async def get_note(run_id: str) -> RunStatus:
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no run '{run_id}'")
    return RunStatus(
        run_id=run.run_id,
        brief=run.brief,
        status=run.status,  # type: ignore[arg-type]
        note=run.note,
        dropped_claims=run.dropped_claims,
        violations=run.violations,
        error=run.error,
    )


@router.get("/notes", response_model=list[RunStatus])
async def list_notes() -> list[RunStatus]:
    return [
        RunStatus(
            run_id=r.run_id, brief=r.brief, status=r.status,  # type: ignore[arg-type]
            note=r.note, dropped_claims=r.dropped_claims, violations=r.violations,
            error=r.error,
        )
        for r in store.recent()
    ]


@router.get("/sources", response_model=SourceResponse)
async def get_source(token: str = Query(min_length=3, max_length=500)) -> SourceResponse:
    """Resolve a citation so a reader can check any claim against its source."""
    return resolve(token)


@router.get("/corpus")
async def corpus() -> dict[str, Any]:
    """What the agent can see. Useful for writing a brief that the corpus supports."""
    async with session() as mcp:
        return await mcp.call("describe_corpus", {"section": "all"})
