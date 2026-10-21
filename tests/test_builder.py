"""Tests for turning registry documents into a runnable agent."""
from __future__ import annotations

import pytest

from nitpick_core.builder import build_agent, build_spec, provider_for
from nitpick_core.errors import BuildError


def resolved(agent: dict, **models: dict) -> dict:
    """A resolved-agent mapping, for the builder."""
    return {
        "agent": agent,
        "llm": models.get("llm"),
        "stt": models.get("stt"),
        "tts": models.get("tts"),
    }


AGENT = {
    "name": "Support",
    "slug": "support",
    "handle": "acme-support",
    "prompt": "You are a support agent.",
    "config": {"language": "en"},
}

LLM = {"name": "GPT-4o", "slug": "gpt-4o", "handle": "openai", "kind": "llm", "config": {}}
STT = {"name": "Whisper", "slug": "whisper", "handle": "openai", "kind": "stt", "config": {}}
TTS = {
    "name": "Nova",
    "slug": "nova",
    "handle": "openai",
    "kind": "tts",
    "config": {"voices": ["nova", "shimmer"]},
}


# ---------------------------------------------------------------------------
# The provider
# ---------------------------------------------------------------------------


def test_a_known_handle_names_the_provider():
    assert provider_for(LLM) == "openai"
    assert provider_for(
        {"slug": "x", "handle": "deepgram", "config": {}}
    ) == "deepgram"


def test_an_unknown_handle_falls_back_to_the_model_name():
    """A registry written before the handle existed still works."""
    assert provider_for({"slug": "gpt-4o", "handle": "gpt-4o", "config": {}}) == "openai"
    assert provider_for({"slug": "nova", "handle": "nova", "config": {}}) == "openai"
    assert (
        provider_for({"slug": "deepgram-nova", "handle": "deepgram-nova", "config": {}})
        == "deepgram"
    )


def test_a_base_url_makes_any_provider_usable():
    model = {"slug": "claude", "handle": "anthropic", "config": {"base_url": "https://x/v1"}}
    assert provider_for(model) == "openai"


def test_an_unknown_provider_is_reported_clearly():
    with pytest.raises(BuildError) as error:
        provider_for({"slug": "mystery", "handle": "mystery", "config": {}})
    assert "does not support" in str(error.value)
    assert "base_url" in str(error.value)


# ---------------------------------------------------------------------------
# The spec
# ---------------------------------------------------------------------------


def test_a_spec_is_built_from_the_documents():
    spec = build_spec(
        resolved(AGENT, llm=LLM, stt=STT, tts=TTS), room_id="room-1"
    )

    assert spec.name == "Support"
    assert spec.prompt == "You are a support agent."
    assert spec.stt == "openai"
    assert spec.llm == "openai:gpt-4o"
    assert spec.tts == "openai:nova"
    assert spec.language == "en"
    assert spec.room_id == "room-1"


def test_a_missing_model_is_reported():
    with pytest.raises(BuildError) as error:
        build_spec(resolved(AGENT, llm=LLM, stt=STT), room_id="demo")
    assert "no text-to-speech model" in str(error.value)


def test_overrides_win():
    spec = build_spec(
        resolved(AGENT, llm=LLM, stt=STT, tts=TTS),
        room_id="demo",
        overrides={"llm": "openai:gpt-4o-mini", "language": "ml"},
    )
    assert spec.llm == "openai:gpt-4o-mini"
    assert spec.language == "ml"


def test_a_base_url_is_carried_through():
    model = dict(LLM, config={"base_url": "https://gateway.test/v1"})
    spec = build_spec(resolved(AGENT, llm=model, stt=STT, tts=TTS), room_id="demo")
    assert spec.llm_base_url == "https://gateway.test/v1"


def test_the_tts_voice_comes_from_the_config():
    model = dict(TTS, config={"voices": ["shimmer", "nova"]})
    spec = build_spec(resolved(AGENT, llm=LLM, stt=STT, tts=model), room_id="demo")
    assert spec.tts == "openai:shimmer"


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


def test_an_agent_is_built_from_the_spec():
    spec = build_spec(resolved(AGENT, llm=LLM, stt=STT, tts=TTS), room_id="demo")
    agent = build_agent(spec)

    assert agent.config.name == "Support"
    assert agent.config.stt == "openai"
    assert agent.config.llm == "openai:gpt-4o"
    assert agent.config.tts == "openai:nova"
    assert agent.config.language == "en"


def test_an_agent_can_share_a_room_manager():
    """One server hosts many agents, each in its own room."""
    from nitpick import RoomManager

    manager = RoomManager()
    spec = build_spec(resolved(AGENT, llm=LLM, stt=STT, tts=TTS), room_id="demo")
    agent = build_agent(spec, manager=manager)

    assert agent.manager is manager


def test_the_agents_config_reaches_the_agent():
    agent_document = dict(AGENT, config={"language": "hi", "vad": "silero", "allow_interruptions": False})
    spec = build_spec(
        resolved(agent_document, llm=LLM, stt=STT, tts=TTS), room_id="demo"
    )
    agent = build_agent(spec)

    assert agent.config.vad == "silero"
    assert agent.config.allow_interruptions is False
