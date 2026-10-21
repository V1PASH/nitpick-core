"""The nitpick-core HTTP API.

Two things live on one port:

* the **control API** — dispatch tasks, look at sessions, browse the agent
  registry
* the **signaling WebSocket** at ``/ws`` — the nitpick room protocol, so
  somebody can talk to a running agent

The browser client is served at ``/``.

    POST /tasks           {"handle": "assistant", "room_id": "demo"}
      -> 202 {"id": "ses-...", "status": "queued", ...}

    GET  /tasks/{id}      the session
    GET  /sessions        every session
    GET  /agents          the agent registry
    GET  /agents/{handle} one agent, resolved
    GET  /health
    WS   /ws              the signaling socket
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from nitpick.signaling.server import SignalingServer

from .config import Config, load_config
from .errors import (
    AgentNotFound,
    BuildError,
    DispatchError,
    ModelNotFound,
    RegistryError,
    SessionNotFound,
)
from .flows import run_flow
from .registry import get_registry
from .runtime import AgentRuntime, get_runtime

log = logging.getLogger(__name__)

__all__ = ["create_app"]


class TaskRequest(BaseModel):
    """A request to run an agent."""

    handle: str = Field(..., description="The agent's handle in the registry")
    room_id: str | None = Field(None, description="The room to join. Defaults to the configured one.")
    overrides: dict[str, Any] = Field(default_factory=dict, description="Overrides for the built agent.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary data to record on the session.")
    wait: bool = Field(False, description="Wait for the session to finish before replying.")


def _encoder() -> dict[type, Any]:
    """Encoders for the types the registry stores.

    minidb documents carry an ``ObjectId`` and a ``created_at`` timestamp;
    FastAPI knows about the timestamp but not about the object id.
    """
    encoders: dict[type, Any] = {}
    try:
        from minidb import ObjectId

        encoders[ObjectId] = str
    except ImportError:  # pragma: no cover - minidb is a hard dependency
        pass
    return encoders


def create_app(
    config: Config | None = None,
    *,
    runtime: AgentRuntime | None = None,
) -> FastAPI:
    """Build the application."""
    settings = config or load_config()
    agent_runtime = runtime or get_runtime(settings)

    # Configure Prefect before anything else touches it, so its state
    # lives where this service can find it.
    from .flows import prefect_settings

    prefect_settings(settings)

    # The signaling server shares the runtime's room manager, so every
    # session is visible on the same socket.
    signaling = SignalingServer(agent_runtime.manager)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await agent_runtime.start()
        try:
            yield
        finally:
            await agent_runtime.stop()

    app = FastAPI(
        title="nitpick-core", version="0.1.0", lifespan=lifespan
    )
    app.state.config = settings
    app.state.runtime = agent_runtime
    app.state.signaling = signaling

    # ------------------------------------------------------------------
    # Control API
    # ------------------------------------------------------------------

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "config": settings.to_dict(),
                "sessions": len(agent_runtime.list_sessions()),
            }
        )

    @app.post("/tasks", status_code=202)
    async def create_task(payload: TaskRequest) -> JSONResponse:
        """Dispatch a task. The agent runtime picks it up."""
        try:
            # The flow is synchronous and takes about a second while Prefect
            # records the run, so it goes in a thread rather than blocking
            # the event loop.
            session = await asyncio.to_thread(
                run_flow,
                payload.handle,
                room_id=payload.room_id,
                overrides=payload.overrides or None,
                metadata=payload.metadata or None,
                runtime=agent_runtime,
                config=settings,
            )
        except DispatchError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (AgentNotFound, ModelNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except BuildError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RegistryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        return JSONResponse(session, status_code=202)

    @app.get("/tasks/{session_id}")
    async def get_task(session_id: str) -> JSONResponse:
        """One session."""
        try:
            session = agent_runtime.get_session(session_id)
        except SessionNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse(session.to_dict())

    @app.get("/sessions")
    async def list_sessions() -> JSONResponse:
        """Every session."""
        sessions = [session.to_dict() for session in agent_runtime.list_sessions()]
        return JSONResponse({"count": len(sessions), "results": sessions})

    @app.get("/sessions/{session_id}")
    async def get_session(session_id: str) -> JSONResponse:
        """One session."""
        try:
            session = agent_runtime.get_session(session_id)
        except SessionNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse(session.to_dict())

    @app.get("/agents")
    async def list_agents() -> JSONResponse:
        """The agent registry."""
        try:
            agents = get_registry(settings).list_agents()
        except RegistryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return JSONResponse(
            jsonable_encoder(
                {"count": len(agents), "results": agents}, custom_encoder=_encoder()
            )
        )

    @app.get("/agents/{handle}")
    async def get_agent(handle: str) -> JSONResponse:
        """One agent, with the models it names resolved."""
        try:
            resolved = get_registry(settings).resolve_agent(handle)
        except AgentNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ModelNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RegistryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return JSONResponse(jsonable_encoder(resolved, custom_encoder=_encoder()))

    # ------------------------------------------------------------------
    # The signaling socket and the browser client
    # ------------------------------------------------------------------

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await _serve_signaling(signaling, websocket)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html = _load_web_client(settings)
        if html is None:
            return HTMLResponse(
                "<h1>nitpick-core</h1><p>The browser client is not installed.</p>"
            )
        return HTMLResponse(html)

    return app


async def _serve_signaling(signaling: SignalingServer, websocket: WebSocket) -> None:
    """Run nitpick's WebSocket protocol on this app's socket."""
    import json

    await websocket.accept()
    participant = signaling.manager.register_participant(websocket)
    await websocket.send_json(
        {
            "type": "connected",
            "participant_id": participant.participant_id,
            "stt": {"provider": "pcm", "transport": "audio_pcm"},
            "audio": {"output_sample_rate": signaling.audio_output_sample_rate},
        }
    )
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "invalid_json",
                        "message": "Payload is not valid JSON",
                    }
                )
                continue
            if not isinstance(payload, dict):
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "invalid_payload",
                        "message": "Payload must be a JSON object",
                    }
                )
                continue
            response = await signaling.handle_message(websocket, payload)
            if response is not None:
                await websocket.send_json(response)
    except WebSocketDisconnect:
        pass
    finally:
        signaling.manager.unregister_participant_by_id(participant.participant_id)


def _load_web_client(config: Config) -> str | None:
    """The browser client.

    nitpick ships one; ``NITPICK_CORE_WEB_ROOT`` overrides it.
    """
    from pathlib import Path

    if config.web_root:
        path = Path(config.web_root)
        if path.is_dir():
            path = path / "index.html"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    try:
        from nitpick.signaling import server as nitpick_server
    except ImportError:  # pragma: no cover - nitpick is a hard dependency
        return None

    path = Path(nitpick_server.__file__).resolve().parent.parent / "web" / "index.html"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None
