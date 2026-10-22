"""Tests for the nitpick-core client."""
from __future__ import annotations

import pytest

from nitpick_core.client import CoreClient, CoreError


class FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or (str(payload) if payload is not None else "")
        self.content = b"x" if payload is not None else b""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeHttpx:
    """A stand-in for httpx.AsyncClient."""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict]] = []

    async def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return self.response

    async def aclose(self) -> None:
        pass


@pytest.fixture
def client(monkeypatch) -> CoreClient:
    instance = CoreClient("http://runtime.test:8100")
    monkeypatch.setattr(
        "nitpick_core.client.httpx", None, raising=False
    )
    return instance


def _with_response(monkeypatch, client: CoreClient, response: FakeResponse) -> FakeHttpx:
    fake = FakeHttpx(response)
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: fake)
    return fake


@pytest.mark.asyncio
async def test_dispatching_an_agent(monkeypatch, client: CoreClient):
    fake = _with_response(
        monkeypatch,
        client,
        FakeResponse(202, {"id": "ses-1", "status": "queued"}),
    )

    result = await client.dispatch("acme-support", room_id="demo")

    assert result["id"] == "ses-1"
    method, path, kwargs = fake.calls[0]
    assert method == "POST"
    assert path == "/tasks"
    assert kwargs["json"] == {
        "handle": "acme-support",
        "room_id": "demo",
    }


@pytest.mark.asyncio
async def test_dispatching_without_a_room(monkeypatch, client: CoreClient):
    fake = _with_response(monkeypatch, client, FakeResponse(202, {"id": "ses-1"}))

    await client.dispatch("acme-support")

    _method, _path, kwargs = fake.calls[0]
    assert "room_id" not in kwargs["json"]


@pytest.mark.asyncio
async def test_an_error_becomes_a_core_error(monkeypatch, client: CoreClient):
    _with_response(
        monkeypatch,
        client,
        FakeResponse(404, {"detail": "no agent with the handle 'nobody'"}),
    )

    with pytest.raises(CoreError) as error:
        await client.dispatch("nobody")

    assert error.value.status_code == 404
    assert "nobody" in str(error.value)


@pytest.mark.asyncio
async def test_a_connection_failure_becomes_a_core_error(monkeypatch, client: CoreClient):
    import httpx

    class Failing:
        async def request(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

        async def aclose(self):
            pass

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: Failing())

    with pytest.raises(CoreError) as error:
        await client.dispatch("acme-support")

    assert "could not reach" in str(error.value)


@pytest.mark.asyncio
async def test_listing_sessions(monkeypatch, client: CoreClient):
    _with_response(
        monkeypatch,
        client,
        FakeResponse(200, {"count": 2, "results": [{"id": "a"}, {"id": "b"}]}),
    )

    sessions = await client.sessions()

    assert sessions == [{"id": "a"}, {"id": "b"}]


@pytest.mark.asyncio
async def test_listing_agents(monkeypatch, client: CoreClient):
    _with_response(
        monkeypatch,
        client,
        FakeResponse(200, {"count": 1, "results": [{"handle": "acme-support"}]}),
    )

    agents = await client.agents()

    assert agents == [{"handle": "acme-support"}]


@pytest.mark.asyncio
async def test_fetching_one_session(monkeypatch, client: CoreClient):
    fake = _with_response(monkeypatch, client, FakeResponse(200, {"id": "ses-1"}))

    result = await client.session("ses-1")

    assert result["id"] == "ses-1"
    assert fake.calls[0][1] == "/sessions/ses-1"
