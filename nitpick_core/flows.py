"""Prefect flows — the dispatcher.

Prefect does one job here: it hands a request to the agent runtime and
records that it did. The conversation itself is owned by the runtime,
because a voice session is long-running and interactive and does not fit
Prefect's bounded-task model.

So a flow is short::

    Django ──POST /tasks──▶ nitpick-core ──▶ Prefect flow
                                                    │
                                                    ├─▶ runtime.dispatch(...)
                                                    └─▶ returns a ticket

The ticket is the session id. Poll ``GET /sessions/{id}`` for the
conversation's progress.

If ``PREFECT_API_URL`` is set an external Prefect server is used. Otherwise
Prefect runs an ephemeral local server, so the service is self-contained.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .config import Config
from .errors import DispatchError
from .runtime import AgentRuntime, get_runtime

log = logging.getLogger(__name__)

__all__ = ["prefect_settings", "run_flow"]

_configured = False
_flow: Any = None


def prefect_settings(config: Config | None = None) -> None:
    """Point Prefect at the right server.

    With ``PREFECT_API_URL`` set, Prefect talks to that server. Without it,
    Prefect uses its ephemeral local server, which keeps the service
    self-contained.

    The settings are applied as **environment variables**, not just as
    Prefect settings: the ephemeral server runs in a subprocess, and it
    only sees the configuration the environment carries. A relative home
    is also made absolute — a service should not depend on its working
    directory.
    """
    global _configured
    if _configured:
        return
    _configured = True

    config = config or Config()

    try:
        from prefect.settings import temporary_settings
    except ImportError:  # pragma: no cover - prefect is a hard dependency
        log.warning("prefect is not installed — flows will not be traced")
        return

    if config.prefect_api_url:
        os.environ["PREFECT_API_URL"] = config.prefect_api_url

    if config.prefect_home:
        home = Path(config.prefect_home).expanduser().resolve()
        os.environ["PREFECT_HOME"] = str(home)
        try:
            home.mkdir(parents=True, exist_ok=True)
        except OSError:  # pragma: no cover - best effort
            log.debug("could not create the prefect home", exc_info=True)

    overrides: dict[str, Any] = {}
    if config.prefect_api_url:
        overrides["PREFECT_API_URL"] = config.prefect_api_url
    if config.prefect_home:
        overrides["PREFECT_HOME"] = os.environ["PREFECT_HOME"]

    if overrides:
        log.info("prefect configured: %s", overrides)
        try:
            temporary_settings(overrides=overrides).__enter__()
        except Exception:  # pragma: no cover - best effort
            log.debug("could not apply the prefect overrides", exc_info=True)


def _build_flow() -> Any:
    """Create the Prefect flow.

    Prefect is imported lazily so that importing nitpick_core does not
    start a Prefect server as a side effect. The flow is built **once** and
    reused: registering a new flow on every call would restart Prefect's
    ephemeral server each time, adding seconds to every dispatch.
    """
    global _flow
    if _flow is not None:
        return _flow

    from prefect import flow

    @flow(name="dispatch-agent-task", log_prints=False)
    def dispatch_agent_task(
        handle: str,
        room_id: str | None = None,
        overrides: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        runtime: AgentRuntime | None = None,
    ) -> dict[str, Any]:
        """Hand a task to the agent runtime.

        Returns a ticket describing the session that was queued.
        """
        target = runtime or get_runtime()
        flow_run_id = _current_flow_run_id()
        try:
            session = _dispatch_nowait(
                target, handle, room_id, overrides, metadata, flow_run_id
            )
        except DispatchError:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced as a failed flow
            raise DispatchError(f"could not dispatch {handle!r}: {exc}") from exc

        log.info("dispatched %r to room %r (%s)", handle, session.room_id, session.id)
        return session.to_dict()

    _flow = dispatch_agent_task
    return _flow


#: The flow object, once it has been built. Importing it as a name would
#: start a Prefect server at import time.
def __getattr__(name: str) -> Any:
    if name == "dispatch_agent_task":
        return _build_flow()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _current_flow_run_id() -> str | None:
    """The id of the flow run that is executing, if any."""
    try:
        from prefect.runtime import flow_run

        return str(flow_run.id) if flow_run is not None else None
    except Exception:  # pragma: no cover - not inside a flow run
        return None


def _dispatch_nowait(
    runtime: AgentRuntime,
    handle: str,
    room_id: str | None,
    overrides: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
    flow_run_id: str | None = None,
) -> Any:
    """Dispatch from inside a synchronous flow.

    The runtime's ``dispatch`` is a coroutine. A Prefect flow is a normal
    function, so the coroutine is run on a private event loop when the
    caller is not already inside one.
    """
    import asyncio

    async def _do() -> Any:
        return await runtime.dispatch(
            handle,
            room_id=room_id,
            overrides=overrides,
            metadata=metadata,
            flow_run_id=flow_run_id,
        )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_do())

    # Already inside a loop (a test, or an async caller): run it in a
    # separate thread with its own loop.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(_do())).result()


def run_flow(
    handle: str,
    *,
    room_id: str | None = None,
    overrides: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    runtime: AgentRuntime | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Run the dispatch flow.

    This is what the HTTP layer calls. It is deliberately synchronous —
    the flow is short, and the session continues in the background.
    """
    prefect_settings(config)
    flow = _build_flow()
    return flow(
        handle,
        room_id=room_id,
        overrides=overrides,
        metadata=metadata,
        runtime=runtime,
    )
