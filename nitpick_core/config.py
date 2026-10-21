"""Configuration for nitpick-core.

Everything is read from the environment, so the service can be configured the
same way in development and in production.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    """The service's settings."""

    #: Where the minidb database with the agent registry lives.
    minidb_path: str = field(
        default_factory=lambda: _env("MINIDB_PATH", "voice.db")
    )
    #: The collections inside it.
    agent_collection: str = field(default_factory=lambda: _env("AGENT_COLLECTION", "agent"))
    model_collection: str = field(default_factory=lambda: _env("MODEL_COLLECTION", "models"))

    #: The HTTP server.
    host: str = field(default_factory=lambda: _env("NITPICK_CORE_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _env_int("NITPICK_CORE_PORT", 8100))

    #: How many agent sessions may run at once.
    max_concurrent_sessions: int = field(
        default_factory=lambda: _env_int("NITPICK_CORE_MAX_SESSIONS", 4)
    )
    #: How long a session may live before it is ended, in seconds.
    session_max_seconds: float = field(
        default_factory=lambda: _env_float("NITPICK_CORE_SESSION_MAX_SECONDS", 900.0)
    )
    #: How long a session waits for somebody to join before giving up.
    session_join_timeout: float = field(
        default_factory=lambda: _env_float("NITPICK_CORE_JOIN_TIMEOUT", 120.0)
    )
    #: How long a session waits after the last person leaves before it ends.
    session_idle_timeout: float = field(
        default_factory=lambda: _env_float("NITPICK_CORE_IDLE_TIMEOUT", 30.0)
    )

    #: Prefect. When ``prefect_api_url`` is set an external server is used;
    #: otherwise Prefect runs an ephemeral local server.
    prefect_api_url: str = field(default_factory=lambda: _env("PREFECT_API_URL"))
    prefect_home: str = field(default_factory=lambda: _env("PREFECT_HOME", ".prefect"))

    #: Where the browser client is served from. Empty means "the one that
    #: ships with nitpick".
    web_root: str = field(default_factory=lambda: _env("NITPICK_CORE_WEB_ROOT"))

    #: The default room an agent joins when a task does not name one.
    default_room: str = field(default_factory=lambda: _env("NITPICK_CORE_DEFAULT_ROOM", "demo"))

    def to_dict(self) -> dict:
        """A JSON-safe view, for the health endpoint."""
        return {
            "minidb_path": self.minidb_path,
            "host": self.host,
            "port": self.port,
            "max_concurrent_sessions": self.max_concurrent_sessions,
            "session_max_seconds": self.session_max_seconds,
            "prefect": self.prefect_api_url or "ephemeral-local",
        }


def load_config() -> Config:
    """Read the configuration from the environment."""
    return Config()
