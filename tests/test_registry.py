"""Tests for reading the agent registry out of minidb."""
from __future__ import annotations

import pytest

from nitpick_core.config import Config
from nitpick_core.errors import AgentNotFound, ModelNotFound, RegistryError
from nitpick_core.registry import Registry

AGENT_SCHEMA = {
    "title": "agent",
    "type": "object",
    "required": ["name", "slug"],
    "properties": {
        "name": {"type": "string"},
        "slug": {"type": "string"},
        "handle": {"type": "string"},
        "prompt": {"type": "string"},
        "stt": {"type": "string"},
        "model": {"type": "string"},
        "tts": {"type": "string"},
        "config": {"type": "object"},
    },
}

MODEL_SCHEMA = {
    "title": "model",
    "type": "object",
    "required": ["name", "slug", "kind"],
    "properties": {
        "name": {"type": "string"},
        "slug": {"type": "string"},
        "handle": {"type": "string"},
        "kind": {"type": "string"},
        "description": {"type": "string"},
        "config": {"type": "object"},
        "active": {"type": "boolean"},
    },
}


@pytest.fixture
def registry() -> Registry:
    """A registry backed by an in-memory database, seeded with one agent."""
    from minidb import Database

    database = Database(":memory:")
    database.create_collection("agent", validator=AGENT_SCHEMA)
    database.create_collection("models", validator=MODEL_SCHEMA)

    database["models"].insert_one(
        {
            "name": "GPT-4o",
            "slug": "gpt-4o",
            "handle": "openai",
            "kind": "llm",
            "config": {},
        }
    )
    database["models"].insert_one(
        {
            "name": "Whisper",
            "slug": "whisper",
            "handle": "openai",
            "kind": "stt",
            "config": {},
        }
    )
    database["models"].insert_one(
        {
            "name": "Nova",
            "slug": "nova",
            "handle": "openai",
            "kind": "tts",
            "config": {"voices": ["nova", "shimmer"]},
        }
    )
    database["agent"].insert_one(
        {
            "name": "Support",
            "slug": "support",
            "handle": "acme-support",
            "prompt": "You are a support agent.",
            "stt": "whisper",
            "model": "gpt-4o",
            "tts": "nova",
            "config": {"language": "en"},
        }
    )

    instance = Registry(Config(minidb_path=":memory:"))
    instance._database = database
    return instance


def test_agents_are_listed(registry: Registry):
    agents = registry.list_agents()
    assert len(agents) == 1
    assert agents[0]["handle"] == "acme-support"


def test_an_agent_is_found_by_its_handle(registry: Registry):
    agent = registry.get_agent_by_handle("acme-support")
    assert agent["name"] == "Support"


def test_an_agent_is_found_by_its_slug_too(registry: Registry):
    """A handle defaults to the slug, so the slug must work as well."""
    agent = registry.get_agent_by_handle("support")
    assert agent["handle"] == "acme-support"


def test_an_unknown_handle_raises(registry: Registry):
    with pytest.raises(AgentNotFound):
        registry.get_agent_by_handle("nobody")


def test_models_are_listed_by_kind(registry: Registry):
    assert len(registry.list_models()) == 3
    assert len(registry.list_models("llm")) == 1
    assert len(registry.list_models("tts")) == 1


def test_resolving_an_agent_pulls_in_its_models(registry: Registry):
    resolved = registry.resolve_agent("acme-support")

    assert resolved["agent"]["slug"] == "support"
    assert resolved["llm"]["slug"] == "gpt-4o"
    assert resolved["stt"]["slug"] == "whisper"
    assert resolved["tts"]["slug"] == "nova"


def test_resolving_an_agent_with_a_missing_model_raises(registry: Registry):
    registry.database["agent"].insert_one(
        {
            "name": "Broken",
            "slug": "broken",
            "handle": "broken",
            "model": "no-such-model",
        }
    )
    with pytest.raises(ModelNotFound):
        registry.resolve_agent("broken")


def test_a_model_of_the_wrong_kind_is_rejected(registry: Registry):
    """An agent's stt must be an stt model, not an llm one."""
    registry.database["agent"].insert_one(
        {
            "name": "Wrong",
            "slug": "wrong",
            "handle": "wrong",
            "stt": "gpt-4o",
        }
    )
    with pytest.raises(RegistryError) as error:
        registry.resolve_agent("wrong")
    assert "not a stt model" in str(error.value)


def test_a_missing_collection_is_reported_clearly(registry: Registry):
    from minidb import Database

    empty = Registry(Config(minidb_path=":memory:"))
    empty._database = Database(":memory:")

    with pytest.raises(RegistryError) as error:
        empty.list_agents()
    assert "does not exist" in str(error.value)
