"""nitpick-core — the agent runtime for nitpick.

Loads agent definitions from a minidb registry, dispatches them through
Prefect, and serves the voice rooms so people can talk to them.

    POST /tasks  {"handle": "assistant"}  ->  a session
    WS   /ws                              ->  talk to it
"""
from __future__ import annotations

from .builder import AgentSpec, build_agent, build_spec
from .client import CoreClient, CoreError
from .config import Config, load_config
from .errors import (
    AgentNotFound,
    BuildError,
    DispatchError,
    ModelNotFound,
    NitpickCoreError,
    RegistryError,
    SessionNotFound,
)
from .registry import Registry, get_registry, resolve_agent
from .runtime import AgentRuntime, Session, SessionStatus, get_runtime

__version__ = "0.1.0"

__all__ = [
    "AgentNotFound",
    "AgentRuntime",
    "AgentSpec",
    "BuildError",
    "Config",
    "CoreClient",
    "CoreError",
    "DispatchError",
    "ModelNotFound",
    "NitpickCoreError",
    "Registry",
    "RegistryError",
    "Session",
    "SessionNotFound",
    "SessionStatus",
    "__version__",
    "build_agent",
    "build_spec",
    "get_registry",
    "get_runtime",
    "load_config",
    "resolve_agent",
]
