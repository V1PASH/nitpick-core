"""The agent runtime — the thing that actually runs the agents.

Prefect **dispatches**; this runtime **processes**. A dispatched task lands
on a queue, a worker picks it up, resolves the agent from the registry,
builds it, joins it to a room, and serves the conversation until it ends.

The split matters: a voice session is long-running and interactive, which
does not fit Prefect's bounded-task model. So the Prefect flow is short —
it hands the request over and returns a ticket — and the session itself is
owned by this runtime, which can hold many of them at once.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from nitpick import RoomManager

from .builder import AgentSpec, build_agent, build_spec
from .config import Config
from .errors import DispatchError
from .registry import Registry

log = logging.getLogger(__name__)

__all__ = ["AgentRuntime", "Session", "SessionStatus", "get_runtime", "set_runtime"]


class SessionStatus(str):
    """The lifecycle states a session moves through."""

    QUEUED = "queued"
    STARTING = "starting"
    WAITING = "waiting"
    RUNNING = "running"
    ENDED = "ended"
    FAILED = "failed"


@dataclass
class Session:
    """One agent session."""

    id: str
    handle: str
    room_id: str
    status: str = SessionStatus.QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    ended_at: float | None = None
    error: str | None = None
    spec: AgentSpec | None = None
    #: The Prefect flow run that dispatched this session, if any.
    flow_run_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "handle": self.handle,
            "room_id": self.room_id,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": (
                self.ended_at - self.started_at
                if self.started_at and self.ended_at
                else None
            ),
            "error": self.error,
            "flow_run_id": self.flow_run_id,
            "agent": self.spec.to_dict() if self.spec else None,
            "metadata": self.metadata,
        }


class AgentRuntime:
    """Owns the shared room manager and the session workers."""

    def __init__(
        self,
        config: Config | None = None,
        *,
        registry: Registry | None = None,
        manager: RoomManager | None = None,
    ) -> None:
        self.config = config or Config()
        self.registry = registry or Registry(self.config)
        self.manager = manager if manager is not None else RoomManager()

        self._queue: asyncio.Queue[Session] = asyncio.Queue()
        self._sessions: dict[str, Session] = {}
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the session workers."""
        if self._running:
            return
        self._running = True
        for index in range(max(1, self.config.max_concurrent_sessions)):
            self._workers.append(
                asyncio.create_task(self._worker(index), name=f"nitpick-worker-{index}")
            )
        log.info(
            "agent runtime started with %d worker(s)", len(self._workers)
        )

    async def stop(self) -> None:
        """Stop the workers and end every live session."""
        self._running = False
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

        for session in list(self._sessions.values()):
            if session.status not in (SessionStatus.ENDED, SessionStatus.FAILED):
                session.status = SessionStatus.ENDED
                session.ended_at = time.time()

    # ------------------------------------------------------------------
    # Dispatching
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        handle: str,
        *,
        room_id: str | None = None,
        overrides: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        flow_run_id: str | None = None,
    ) -> Session:
        """Hand a task to the runtime.

        This is what the Prefect flow calls. It returns as soon as the task
        is queued — the session runs in the background.

        The agent is resolved *before* the task is queued, so a handle that
        does not exist fails here rather than silently in a worker.
        """
        if not handle:
            raise DispatchError("a handle is required")

        resolved_room = room_id or self.config.default_room

        # The registry is a blocking file-backed store, so it is read in a
        # thread rather than on the event loop.
        resolved = await asyncio.to_thread(self.registry.resolve_agent, handle)
        spec = build_spec(resolved, room_id=resolved_room, overrides=overrides)

        async with self._lock:
            session = Session(
                id=f"ses-{uuid.uuid4().hex[:12]}",
                handle=handle,
                room_id=resolved_room,
                flow_run_id=flow_run_id,
                metadata=dict(metadata or {}),
                spec=spec,
            )
            self._sessions[session.id] = session

        self._queue.put_nowait(session)
        log.info("dispatched agent %r to room %r (%s)", handle, session.room_id, session.id)
        return session

    def get_session(self, session_id: str) -> Session:
        """One session, by id."""
        session = self._sessions.get(session_id)
        if session is None:
            from .errors import SessionNotFound

            raise SessionNotFound(f"no session called {session_id!r}")
        return session

    def list_sessions(self) -> list[Session]:
        """Every session this runtime knows about."""
        return sorted(self._sessions.values(), key=lambda s: s.created_at, reverse=True)

    # ------------------------------------------------------------------
    # The worker
    # ------------------------------------------------------------------

    async def _worker(self, index: int) -> None:
        """Pull tasks off the queue and run them."""
        while True:
            session = await self._queue.get()
            try:
                await self._run_session(session)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a failed session must not kill the worker
                log.exception("session %s failed", session.id)
                session.status = SessionStatus.FAILED
                session.error = str(exc)
                session.ended_at = time.time()
            finally:
                self._queue.task_done()

    async def _run_session(self, session: Session) -> None:
        """Resolve the agent, run it, and end it when the conversation is over."""
        session.status = SessionStatus.STARTING
        session.started_at = time.time()

        # The agent was resolved when the task was dispatched, so a bad
        # handle never reaches here.
        spec = session.spec
        if spec is None:
            resolved = await asyncio.to_thread(
                self.registry.resolve_agent, session.handle
            )
            spec = build_spec(resolved, room_id=session.room_id)
            session.spec = spec

        agent = build_agent(spec, manager=self.manager, session_id=session.id)
        task = agent.build_pipeline(session.room_id)

        session.status = SessionStatus.WAITING
        task.start()
        log.info(
            "session %s: agent %r joined room %r", session.id, spec.name, session.room_id
        )

        try:
            await self._supervise(session, task)
        finally:
            await task.cancel()
            with_suppress = getattr(task, "wait", None)
            if with_suppress is not None:
                try:
                    await with_suppress()
                except Exception:  # pragma: no cover - the pipeline is stopping
                    pass
            if session.status not in (SessionStatus.ENDED, SessionStatus.FAILED):
                session.status = SessionStatus.ENDED
                session.ended_at = time.time()
            log.info("session %s ended (%s)", session.id, session.status)

    async def _supervise(self, session: Session, task: Any) -> None:
        """Wait for the session to end.

        The session ends when the pipeline finishes, when nobody joins in
        time, when the room empties, or when it has run for too long.
        """
        deadline = time.monotonic() + self.config.session_max_seconds
        joined_deadline = time.monotonic() + self.config.session_join_timeout
        idle_since: float | None = None

        while True:
            if task.finish_reason is not None:
                session.status = SessionStatus.ENDED
                session.ended_at = time.time()
                return

            if time.monotonic() > deadline:
                log.info("session %s reached its maximum duration", session.id)
                await task.end()
                session.status = SessionStatus.ENDED
                session.ended_at = time.time()
                return

            clients = self._client_count(session.room_id)
            if clients:
                session.status = SessionStatus.RUNNING
                idle_since = None
            else:
                if session.status is SessionStatus.RUNNING:
                    if idle_since is None:
                        idle_since = time.monotonic()
                    elif (
                        time.monotonic() - idle_since
                        > self.config.session_idle_timeout
                    ):
                        log.info("session %s: everybody left", session.id)
                        await task.end()
                        session.status = SessionStatus.ENDED
                        session.ended_at = time.time()
                        return
                elif time.monotonic() > joined_deadline:
                    log.info("session %s: nobody joined in time", session.id)
                    await task.end()
                    session.status = SessionStatus.ENDED
                    session.ended_at = time.time()
                    return

            await asyncio.sleep(0.5)

    def _client_count(self, room_id: str) -> int:
        """How many non-agent participants are in the room."""
        room = self.manager.get_room(room_id)
        if room is None:
            return 0
        return sum(
            1
            for participant in room.participants
            if not participant.is_agent and participant.websocket is not None
        )


#: The process-wide runtime.
_runtime: AgentRuntime | None = None


def get_runtime(config: Config | None = None) -> AgentRuntime:
    """The shared runtime."""
    global _runtime
    if _runtime is None:
        _runtime = AgentRuntime(config)
    return _runtime


def set_runtime(runtime: AgentRuntime | None) -> None:
    """Replace the shared runtime. Useful in tests."""
    global _runtime
    _runtime = runtime
