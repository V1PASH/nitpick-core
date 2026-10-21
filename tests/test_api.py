"""Tests for the HTTP API."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from nitpick_core.api import create_app
from nitpick_core.config import Config
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
def runtime(registry: Registry) -> AgentRuntime:
    return AgentRuntime(
        Config(
            minidb_path=":memory:",
            session_join_timeout=1.0,
            session_idle_timeout=1.0,
        ),
        registry=registry,
    )


@pytest.fixture
def client(runtime: AgentRuntime, registry: Registry, monkeypatch):
    """A test client whose dispatch does not need Prefect.

    The Prefect path has its own test; here we only care about the HTTP
    surface, and starting an ephemeral Prefect server for every request
    would make the suite crawl.
    """
    import nitpick_core.api as api
    import nitpick_core.flows as flows

    def fake_run_flow(handle, **kwargs):
        """The real flow is synchronous, so the fake is too."""
        session = asyncio.run(
            runtime.dispatch(
                handle,
                room_id=kwargs.get("room_id"),
                overrides=kwargs.get("overrides"),
                metadata=kwargs.get("metadata"),
            )
        )
        return session.to_dict()

    monkeypatch.setattr(api, "run_flow", fake_run_flow)
    monkeypatch.setattr(flows, "run_flow", fake_run_flow)

    app = create_app(Config(minidb_path=":memory:"), runtime=runtime)
    # The registry the API uses is the shared one.
    import nitpick_core.registry as registry_module

    monkeypatch.setattr(registry_module, "_registry", registry)
    monkeypatch.setattr(api, "get_registry", lambda *a, **k: registry)

    with TestClient(app) as test_client:
        yield test_client


def test_health(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "config" in body


def test_agents_are_listed(client: TestClient):
    response = client.get("/agents")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["results"][0]["handle"] == "acme-support"


def test_one_agent_is_resolved(client: TestClient):
    response = client.get("/agents/acme-support")
    assert response.status_code == 200
    body = response.json()
    assert body["agent"]["slug"] == "support"
    assert body["llm"]["slug"] == "gpt-4o"
    assert body["stt"]["slug"] == "whisper"
    assert body["tts"]["slug"] == "nova"


def test_an_unknown_agent_is_a_404(client: TestClient):
    response = client.get("/agents/nobody")
    assert response.status_code == 404


def test_a_task_is_accepted(client: TestClient):
    response = client.post("/tasks", json={"handle": "acme-support", "room_id": "room-1"})
    assert response.status_code == 202

    body = response.json()
    assert body["handle"] == "acme-support"
    assert body["room_id"] == "room-1"
    assert body["id"].startswith("ses-")


def test_a_task_for_an_unknown_agent_is_a_404(client: TestClient):
    response = client.post("/tasks", json={"handle": "nobody"})
    assert response.status_code == 404


def test_a_task_without_a_handle_is_rejected(client: TestClient):
    response = client.post("/tasks", json={})
    assert response.status_code == 422


def test_a_task_is_fetchable(client: TestClient):
    created = client.post("/tasks", json={"handle": "acme-support"}).json()

    response = client.get(f"/tasks/{created['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_an_unknown_task_is_a_404(client: TestClient):
    response = client.get("/tasks/ses-nope")
    assert response.status_code == 404


def test_sessions_are_listed(client: TestClient):
    client.post("/tasks", json={"handle": "acme-support"})

    response = client.get("/sessions")
    assert response.status_code == 200
    assert response.json()["count"] >= 1


def test_the_browser_client_is_served(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "nitpick" in response.text.lower()


def test_the_signaling_socket_accepts_a_connection(client: TestClient):
    with client.websocket_connect("/ws") as websocket:
        message = websocket.receive_json()
        assert message["type"] == "connected"
        assert message["participant_id"].startswith("p-")
        assert message["audio"]["output_sample_rate"] == 24000
