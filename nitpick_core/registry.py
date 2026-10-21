"""Reading the agent registry out of minidb.

The registry lives in the same minidb database the Django service writes to.
Two collections matter:

``agent``
    The agents themselves — a name, a prompt, and the three model slugs it
    is wired together from.
``models``
    The registry of models an agent can be built from — an LLM, a
    text-to-speech model, a speech-to-text model.

Every document carries a ``handle``: a stable, human-meaningful identifier
(the provider for a model, the agent's own name for an agent). The Django
API is keyed by ``slug``; this module looks up by ``handle`` and falls back
to the slug, because a handle defaults to the slug when it is not set
explicitly.
"""
from __future__ import annotations

import threading
from typing import Any

from .config import Config
from .errors import AgentNotFound, ModelNotFound, RegistryError

__all__ = [
    "get_database",
    "get_agent",
    "get_agent_by_handle",
    "get_model",
    "get_model_by_handle",
    "list_agents",
    "list_models",
    "resolve_agent",
    "set_database",
    "reset_database",
]


class Registry:
    """The agent registry.

    The database handle is opened lazily and reused. minidb is not safe to
    share across threads for writes, so writes are serialised with a lock;
    reads are guarded by the same lock for simplicity.
    """

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        self._database: Any = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # The database
    # ------------------------------------------------------------------

    @property
    def database(self) -> Any:
        """The minidb database, opened on first use."""
        with self._lock:
            if self._database is None:
                try:
                    from minidb import Database
                except ImportError as exc:  # pragma: no cover - import guard
                    raise RegistryError(
                        "minidb is not installed. Install it with: "
                        "pip install nitpick-core[minidb]"
                    ) from exc
                self._database = Database(self.config.minidb_path)
            return self._database

    def close(self) -> None:
        """Close the database handle."""
        with self._lock:
            if self._database is not None:
                try:
                    self._database.close()
                except Exception:  # pragma: no cover - best effort
                    pass
                self._database = None

    def _collection(self, name: str) -> Any:
        database = self.database
        # minidb follows PyMongo and creates a collection on first use, so
        # a missing one has to be checked for explicitly.
        if name not in database:
            raise RegistryError(
                f"the collection {name!r} does not exist in "
                f"{self.config.minidb_path!r} — has the service been seeded?"
            )
        return database[name]

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def list_agents(self) -> list[dict]:
        """Every agent in the registry."""
        with self._lock:
            return list(self._collection(self.config.agent_collection).find({}))

    def list_models(self, kind: str | None = None) -> list[dict]:
        """Every model in the registry, optionally of one kind."""
        with self._lock:
            query = {"kind": kind} if kind else {}
            return list(self._collection(self.config.model_collection).find(query))

    def get_agent(self, slug: str) -> dict:
        """One agent, by slug."""
        with self._lock:
            document = self._collection(self.config.agent_collection).find_one(
                {"slug": slug}
            )
        if document is None:
            raise AgentNotFound(f"no agent called {slug!r}")
        return document

    def get_agent_by_handle(self, handle: str) -> dict:
        """One agent, by handle.

        Falls back to the slug, because a handle defaults to the slug when
        it was not set explicitly.
        """
        with self._lock:
            collection = self._collection(self.config.agent_collection)
            document = collection.find_one({"handle": handle})
            if document is None:
                document = collection.find_one({"slug": handle})
        if document is None:
            raise AgentNotFound(f"no agent with the handle {handle!r}")
        return document

    def get_model(self, slug: str) -> dict:
        """One model, by slug."""
        with self._lock:
            document = self._collection(self.config.model_collection).find_one(
                {"slug": slug}
            )
        if document is None:
            raise ModelNotFound(f"no model called {slug!r}")
        return document

    def get_model_by_handle(self, handle: str, kind: str | None = None) -> dict | None:
        """One model, by handle — the first match, optionally of one kind."""
        with self._lock:
            collection = self._collection(self.config.model_collection)
            query: dict[str, Any] = {"handle": handle}
            if kind:
                query["kind"] = kind
            document = collection.find_one(query)
            if document is None and not kind:
                document = collection.find_one({"slug": handle})
        return document

    # ------------------------------------------------------------------
    # Resolving
    # ------------------------------------------------------------------

    def resolve_agent(self, handle: str) -> dict:
        """The agent document plus the model documents it names.

        The result is::

            {
              "agent": {...},          # the agent document
              "stt": {...} | None,     # the speech-to-text model
              "llm": {...} | None,     # the language model
              "tts": {...} | None,     # the text-to-speech model
            }
        """
        agent = self.get_agent_by_handle(handle)

        resolved: dict[str, Any] = {"agent": agent, "stt": None, "llm": None, "tts": None}

        # The agent names its models by slug.
        for field, kind in (("stt", "stt"), ("model", "llm"), ("tts", "tts")):
            slug = agent.get(field)
            if not slug:
                continue
            model = self.get_model(slug)
            if model.get("kind") != kind:
                raise RegistryError(
                    f"the agent's {field} is {slug!r}, which is a "
                    f"{model.get('kind')} model, not a {kind} model"
                )
            resolved[kind] = model

        return resolved


#: The process-wide registry.
_registry: Registry | None = None


def get_registry(config: Config | None = None) -> Registry:
    """The shared registry."""
    global _registry
    if _registry is None:
        _registry = Registry(config)
    return _registry


def set_database(database: Any) -> None:
    """Use a specific database. Useful in tests."""
    get_registry()._database = database


def reset_database() -> None:
    """Forget the cached database handle."""
    get_registry().close()


def get_database() -> Any:
    """The shared registry's database."""
    return get_registry().database


def get_agent(handle: str) -> dict:
    """One agent, by handle."""
    return get_registry().get_agent_by_handle(handle)


def get_agent_by_handle(handle: str) -> dict:
    """One agent, by handle."""
    return get_registry().get_agent_by_handle(handle)


def get_model(slug: str) -> dict:
    """One model, by slug."""
    return get_registry().get_model(slug)


def get_model_by_handle(handle: str, kind: str | None = None) -> dict | None:
    """One model, by handle."""
    return get_registry().get_model_by_handle(handle, kind)


def list_agents() -> list[dict]:
    """Every agent in the registry."""
    return get_registry().list_agents()


def list_models(kind: str | None = None) -> list[dict]:
    """Every model in the registry."""
    return get_registry().list_models(kind)


def resolve_agent(handle: str) -> dict:
    """The agent document plus the models it names."""
    return get_registry().resolve_agent(handle)
