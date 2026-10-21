"""Errors raised by nitpick-core."""
from __future__ import annotations

__all__ = [
    "AgentNotFound",
    "BuildError",
    "DispatchError",
    "ModelNotFound",
    "NitpickCoreError",
    "RegistryError",
    "SessionNotFound",
]


class NitpickCoreError(Exception):
    """Base class for every error this service raises."""


class RegistryError(NitpickCoreError):
    """The agent registry could not be read."""


class AgentNotFound(RegistryError):
    """No agent with that handle or slug."""


class ModelNotFound(RegistryError):
    """No model with that slug."""


class BuildError(NitpickCoreError):
    """The registry documents cannot be turned into a runnable agent."""


class DispatchError(NitpickCoreError):
    """A task could not be dispatched."""


class SessionNotFound(NitpickCoreError):
    """No session with that id."""
