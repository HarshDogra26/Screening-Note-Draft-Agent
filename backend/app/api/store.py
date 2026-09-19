"""In-memory run store.
"""

from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from typing import Any
from ..logging import get_logger

log = get_logger(__name__)

MAX_RUNS = 100


@dataclass
class Run:
    run_id: str
    brief: str
    status: str = "running"
    events: list[tuple[str, Any]] = field(default_factory=list)
    note: dict[str, Any] | None = None
    dropped_claims: list[dict[str, Any]] = field(default_factory=list)
    violations: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    _subscribers: list[asyncio.Queue] = field(default_factory=list)

    def publish(self, kind: str, payload: Any) -> None:
        self.events.append((kind, payload))
        for queue in self._subscribers:
            queue.put_nowait((kind, payload))

    def subscribe(self) -> asyncio.Queue:
        """Replays everything so far, then follows live."""
        queue: asyncio.Queue = asyncio.Queue()
        for event in self.events:
            queue.put_nowait(event)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def finish(self, status: str, error: str | None = None) -> None:
        self.status = status
        self.error = error
        self.publish("closed", {"status": status, "error": error})


class RunStore:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}

    def create(self, run_id: str, brief: str) -> Run:
        run = Run(run_id=run_id, brief=brief)
        self._runs[run_id] = run
        # Bound memory: drop the oldest once past the cap.
        while len(self._runs) > MAX_RUNS:
            oldest = next(iter(self._runs))
            del self._runs[oldest]
        return run

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def recent(self, limit: int = 20) -> list[Run]:
        return list(self._runs.values())[-limit:][::-1]


store = RunStore()
