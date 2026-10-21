"""Tests for the Prefect dispatch flow.

These run the real flow, against a real (ephemeral) Prefect server, so they
are slower than the rest of the suite.
"""
from __future__ import annotations

import pytest

from nitpick_core.config import Config
from nitpick_core.errors import AgentNotFound
from nitpick_core.flows import run_flow
from nitpick_core.registry import Registry
from nitpick_core.runtime import AgentRuntime

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
def runtime() -> AgentRuntime:
    from minidb import Database

    database = Database(":memory:")
    database.create_collection("agent")
    database.create_collection("models")
    for model in MODELS:
        database["models"].insert_one(dict(model))
    database["agent"].insert_one(dict(AGENT))

    registry = Registry(Config(minidb_path=":memory:"))
    registry._database = database

    return AgentRuntime(
        Config(minidb_path=":memory:", session_join_timeout=1.0),
        registry=registry,
    )


@pytest.mark.slow
def test_the_flow_dispatches_a_task(runtime: AgentRuntime):
    result = run_flow("acme-support", room_id="room-1", runtime=runtime)

    assert result["handle"] == "acme-support"
    assert result["room_id"] == "room-1"
    assert result["id"].startswith("ses-")
    assert result["status"] in ("queued", "starting", "waiting", "running")
    # Prefect records the run, and the session knows which one.
    assert result["flow_run_id"], "the session must record its flow run"


@pytest.mark.slow
def test_the_flow_reports_an_unknown_handle(runtime: AgentRuntime):
    from nitpick_core.errors import DispatchError

    with pytest.raises((AgentNotFound, DispatchError)):
        run_flow("nobody", room_id="room-1", runtime=runtime)


@pytest.mark.slow
def test_the_flow_is_reused(runtime: AgentRuntime):
    """The flow must be registered once.

    Registering it per call restarts Prefect's ephemeral server every
    time, which adds seconds to every dispatch.
    """
    from nitpick_core import flows

    first = flows._build_flow()
    second = flows._build_flow()
    assert first is second
