"""Turning registry documents into a runnable nitpick agent.

The registry stores *what* an agent is — its prompt and the models it talks
through — in a provider-neutral way. This module translates that into the
provider-specific strings nitpick's ``Agent`` facade expects.

The translation is driven by the model's ``handle``, which names the
provider (``openai``, ``deepgram``, ``sarvam``, ...):

============  ==========  ==========================================
Registry      nitpick     How the value is derived
============  ==========  ==========================================
``llm``       ``llm``     ``openai:<model>``
``stt``       ``stt``     ``openai`` / ``deepgram`` / ``sarvam``
``tts``       ``tts``     ``openai:<voice>``
============  ==========  ==========================================

A model may override the derived value through its ``config`` — for
example ``{"model": "gpt-4o-mini"}`` on an LLM, or ``{"voices": ["nova"]}``
on a TTS model.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from nitpick import Agent

from .errors import BuildError

log = logging.getLogger(__name__)

__all__ = ["AgentSpec", "build_agent", "build_spec", "provider_for"]


#: Handles nitpick can talk to directly.
SUPPORTED_PROVIDERS = {
    "openai": "openai",
    "deepgram": "deepgram",
    "sarvam": "sarvam",
    "ollama": "openai",  # OpenAI-compatible wire format
    "vllm": "openai",
    "together": "openai",
    "groq": "openai",
}

#: A fallback for registries where a model's handle was left as its slug
#: instead of naming the provider. The handle is the right way to say this;
#: the table only rescues data that was seeded before the handle was
#: introduced, or written by hand.
KNOWN_MODEL_PROVIDERS = {
    "gpt-4o": "openai",
    "gpt-4o-mini": "openai",
    "gpt-4.1": "openai",
    "gpt-4-turbo": "openai",
    "o1": "openai",
    "o3": "openai",
    "alloy": "openai",
    "echo": "openai",
    "fable": "openai",
    "onyx": "openai",
    "nova": "openai",
    "whisper": "openai",
    "deepgram": "deepgram",
    "deepgram-nova": "deepgram",
    "nova-3": "deepgram",
    "saaras": "sarvam",
    "saaras:v3": "sarvam",
    "sarvam": "sarvam",
}


@dataclass
class AgentSpec:
    """Everything needed to build one agent, resolved from the registry."""

    handle: str
    name: str
    prompt: str
    stt: str
    llm: str
    tts: str
    language: str | None = None
    #: The base URL of the LLM endpoint, for OpenAI-compatible servers.
    llm_base_url: str | None = None
    room_id: str = "demo"
    config: dict[str, Any] = field(default_factory=dict)
    #: The raw documents, kept for the session record.
    documents: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "name": self.name,
            "prompt": self.prompt,
            "stt": self.stt,
            "llm": self.llm,
            "tts": self.tts,
            "language": self.language,
            "llm_base_url": self.llm_base_url,
            "room_id": self.room_id,
            "config": self.config,
        }


def provider_for(model: dict[str, Any]) -> str:
    """The nitpick provider name for a registry model.

    The model's ``handle`` names the provider. A model whose provider
    nitpick does not know about is only usable if it declares an
    OpenAI-compatible ``base_url``.
    """
    handle = str(model.get("handle") or "").strip().lower()
    config = model.get("config") or {}

    if handle in SUPPORTED_PROVIDERS:
        return SUPPORTED_PROVIDERS[handle]

    # The handle was left as the model's own name — fall back to a lookup
    # by name, so a registry written before the handle existed still works.
    for key in ("slug", "name"):
        candidate = str(model.get(key) or "").strip().lower()
        if candidate in KNOWN_MODEL_PROVIDERS:
            return KNOWN_MODEL_PROVIDERS[candidate]

    if config.get("base_url"):
        # Any OpenAI-compatible endpoint works.
        return "openai"

    raise BuildError(
        f"the model {model.get('slug')!r} uses the provider {handle!r}, which "
        "nitpick does not support. Supported providers are: "
        + ", ".join(sorted(set(SUPPORTED_PROVIDERS.values())))
        + ". Give the model a 'base_url' in its config if it speaks the "
        "OpenAI wire format, or set its handle to the provider's name."
    )


def _model_id(model: dict[str, Any], fallback: str) -> str:
    """The provider-specific model identifier."""
    config = model.get("config") or {}
    for key in ("model", "model_id", "id"):
        value = config.get(key)
        if isinstance(value, str) and value:
            return value
    # The slug is a better identifier than the display name.
    slug = model.get("slug")
    if isinstance(slug, str) and slug:
        return slug
    return fallback


def _voice_for(model: dict[str, Any]) -> str:
    """The voice a TTS model should speak with."""
    config = model.get("config") or {}
    for key in ("voice", "voices"):
        value = config.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, str) and first:
                return first
    slug = model.get("slug")
    return slug if isinstance(slug, str) and slug else "alloy"


def _stt_spec(model: dict[str, Any]) -> str:
    """The ``stt`` argument for nitpick's Agent."""
    provider = provider_for(model)
    if provider == "openai":
        return "openai"
    if provider == "deepgram":
        return "deepgram"
    if provider == "sarvam":
        return "sarvam"
    raise BuildError(f"unsupported speech-to-text provider {provider!r}")


def _llm_spec(model: dict[str, Any]) -> str:
    """The ``llm`` argument for nitpick's Agent."""
    provider = provider_for(model)
    if provider != "openai":
        raise BuildError(
            f"unsupported language-model provider {provider!r} — nitpick speaks "
            "the OpenAI wire format"
        )
    return f"openai:{_model_id(model, 'gpt-4o-mini')}"


def _tts_spec(model: dict[str, Any]) -> str:
    """The ``tts`` argument for nitpick's Agent."""
    provider = provider_for(model)
    if provider != "openai":
        raise BuildError(f"unsupported text-to-speech provider {provider!r}")
    return f"openai:{_voice_for(model)}"


def build_spec(
    resolved: dict[str, Any],
    *,
    room_id: str = "demo",
    overrides: dict[str, Any] | None = None,
) -> AgentSpec:
    """Build the spec for one agent from its resolved documents."""
    agent_document = resolved["agent"]
    config = dict(agent_document.get("config") or {})
    overrides = overrides or {}

    stt_model = resolved.get("stt")
    llm_model = resolved.get("llm")
    tts_model = resolved.get("tts")

    if stt_model is None:
        raise BuildError(
            f"the agent {agent_document.get('slug')!r} has no speech-to-text model"
        )
    if llm_model is None:
        raise BuildError(
            f"the agent {agent_document.get('slug')!r} has no language model"
        )
    if tts_model is None:
        raise BuildError(
            f"the agent {agent_document.get('slug')!r} has no text-to-speech model"
        )

    stt = overrides.get("stt") or _stt_spec(stt_model)
    llm = overrides.get("llm") or _llm_spec(llm_model)
    tts = overrides.get("tts") or _tts_spec(tts_model)

    language = (
        overrides.get("language")
        or config.get("language")
        or (stt_model.get("config") or {}).get("language")
    )

    # A model may point at an OpenAI-compatible endpoint — a self-hosted
    # model, or a gateway. That is how a provider nitpick does not know
    # about can still be used.
    llm_base_url = (
        overrides.get("llm_base_url")
        or config.get("base_url")
        or (llm_model.get("config") or {}).get("base_url")
    )

    return AgentSpec(
        handle=str(agent_document.get("handle") or agent_document.get("slug") or ""),
        name=str(agent_document.get("name") or agent_document.get("slug") or "Agent"),
        prompt=str(agent_document.get("prompt") or ""),
        stt=stt,
        llm=llm,
        tts=tts,
        language=language if isinstance(language, str) else None,
        llm_base_url=llm_base_url if isinstance(llm_base_url, str) else None,
        room_id=room_id,
        config=config,
        documents=resolved,
    )


def build_agent(
    spec: AgentSpec,
    *,
    manager: Any = None,
    session_id: str | None = None,
) -> Agent:
    """Build the runnable nitpick agent for a spec."""
    kwargs: dict[str, Any] = {
        "name": spec.name,
        "prompt": spec.prompt or None,
        "stt": spec.stt,
        "llm": spec.llm,
        "tts": spec.tts,
    }
    if spec.language:
        kwargs["language"] = spec.language
    if spec.llm_base_url:
        kwargs["llm_base_url"] = spec.llm_base_url
    if manager is not None:
        kwargs["manager"] = manager
    if session_id:
        kwargs["session_id"] = session_id

    # The agent's own config may carry pipeline settings.
    config = spec.config or {}
    if "vad" in config:
        kwargs["vad"] = config["vad"]
    if "allow_interruptions" in config:
        kwargs["allow_interruptions"] = bool(config["allow_interruptions"])

    return Agent(**kwargs)
