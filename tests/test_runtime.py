"""Tests for the runtime: dispatching a task and running the session."""
from __future__ import annotations

import asyncio

import pytest

from nitpick_core.config import Config
from nitpick_core.errors import DispatchError, SessionNotFound
from nitpick_core.registry import Registry
from nitpick_core.runtime import AgentRuntime, SessionStatus

AGENT = {
    "name": "Support",
    "slug": "support",
    "handle": "acme-support",
    "prompt": "You are a support agent.",
    "stt": "whisper",
    "model": "gpt-4o",
    "tts": "nova",
    "config": {},
}

MODELS = [
    {"name": "GPT-4o", "slug": "gpt-4o", "handle": "openai", "kind": "llm", "config": {}},
    {"name": "Whisper", "slug": "whisper", "handle": "openai", "kind": "stt", "config": {}},
    {"name": "Nova", "slug": "nova", "handle": "openai", "kind": "tts", "config": {}},
]


@pytest.fixture
def registry() -> Registry:
    from minidb import Database

    database = Database(":memory:")
    database.create_collection("agent")
    database.create_collection("models")
    for model in MODELS:
        database["models"].insert_one(dict(model))
    database["agent"].insert_one(dict(AGENT))

    instance = Registry(Config(minidb_path=":memory:"))
    instance._database = database
    return instance


@pytest.fixture
async def runtime(registry: Registry):
    """A runtime with short timeouts, so the tests do not wait."""
    config = Config(
        minidb_path=":memory:",
        max_concurrent_sessions=2,
        session_join_timeout=1.0,
        session_idle_timeout=1.0,
        session_max_seconds=10.0,
    )
    instance = AgentRuntime(config, registry=registry)
    await instance.start()
    yield instance
    await instance.stop()


@pytest.mark.asyncio
async def test_dispatching_creates_a_session(runtime: AgentRuntime):
    session = await runtime.dispatch("acme-support", room_id="room-1")

    assert session.handle == "acme-support"
    assert session.room_id == "room-1"
    assert session.status == SessionStatus.QUEUED
    assert session.id.startswith("ses-")


@pytest.mark.asyncio
async def test_a_dispatched_session_joins_its_room(runtime: AgentRuntime):
    session = await runtime.dispatch("acme-support", room_id="room-1")

    # Give the worker a moment to pick it up.
    for _ in range(50):
        await asyncio.sleep(0.1)
        if session.status in (SessionStatus.WAITING, SessionStatus.RUNNING):
            break

    assert session.status in (SessionStatus.WAITING, SessionStatus.RUNNING)
    assert session.spec is not None
    assert session.spec.llm == "openai:gpt-4o"

    # The agent is in the room, as an agent.
    room = runtime.manager.get_room("room-1")
    assert room is not None
    assert any(participant.is_agent for participant in room.participants)


@pytest.mark.asyncio
async def test_an_unknown_handle_fails_at_dispatch(runtime: AgentRuntime):
    """A bad handle must fail immediately, not in a worker.

    The agent is resolved before the task is queued, so the caller gets an
    error straight away instead of a session that quietly fails.
    """
    from nitpick_core.errors import AgentNotFound

    with pytest.raises(AgentNotFound):
        await runtime.dispatch("nobody", room_id="room-1")

    assert runtime.list_sessions() == []


@pytest.mark.asyncio
async def test_a_session_ends_when_nobody_joins(runtime: AgentRuntime):
    session = await runtime.dispatch("acme-support", room_id="room-empty")

    for _ in range(80):
        await asyncio.sleep(0.1)
        if session.status == SessionStatus.ENDED:
            break

    assert session.status == SessionStatus.ENDED
    assert session.ended_at is not None


@pytest.mark.asyncio
async def test_sessions_are_listed_and_fetched(runtime: AgentRuntime):
    session = await runtime.dispatch("acme-support", room_id="room-1")

    assert session in runtime.list_sessions()
    assert runtime.get_session(session.id) is session

    with pytest.raises(SessionNotFound):
        runtime.get_session("ses-nope")


@pytest.mark.asyncio
async def test_a_dispatch_without_a_handle_is_rejected(runtime: AgentRuntime):
    with pytest.raises(DispatchError):
        await runtime.dispatch("")
