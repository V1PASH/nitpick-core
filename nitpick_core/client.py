"""A typed client for the nitpick-core service.

The service's HTTP API is small, but calling it by hand from another
service means writing the same four lines everywhere. This is the same
idea as nitpick's own :class:`~nitpick.sdk.RoomClient`: a small, typed
wrapper so a caller says what it means.

    from nitpick_core import CoreClient

    async with CoreClient("http://127.0.0.1:8100") as client:
        session = await client.dispatch("acme-support", room_id="demo")
        print(session["id"], session["status"])
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

__all__ = ["CoreClient", "CoreError"]


class CoreError(RuntimeError):
    """The nitpick-core service could not do what was asked.

    ``status_code`` carries the HTTP status, when there was one.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class CoreClient:
    """An async client for nitpick-core.

    Parameters
    ----------
    base_url:
        The service's base URL, e.g. ``http://127.0.0.1:8100``.
    timeout:
        How long to wait for a reply, in seconds.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8100", *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: Any = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "CoreClient":
        import httpx

        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout)
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get_client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout)
        return self._client

    # ------------------------------------------------------------------
    # Calling
    # ------------------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        import httpx

        client = await self._get_client()
        try:
            response = await client.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            raise CoreError(f"could not reach nitpick-core at {self.base_url}: {error}") from error

        if response.status_code >= 400:
            detail = response.text
            try:
                body = response.json()
                detail = body.get("detail") or body.get("error") or detail
            except Exception:  # noqa: BLE001 - the body may not be JSON
                pass
            raise CoreError(str(detail), status_code=response.status_code)

        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except Exception:  # noqa: BLE001
            return response.text

    # ------------------------------------------------------------------
    # The API
    # ------------------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        """Is the service up?"""
        return await self._request("GET", "/health")

    async def dispatch(
        self,
        handle: str,
        *,
        room_id: str | None = None,
        overrides: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Queue an agent to run.

        Returns the session ticket. The agent is resolved before the task is
        queued, so an unknown handle fails here rather than later.
        """
        payload: dict[str, Any] = {"handle": handle}
        if room_id:
            payload["room_id"] = room_id
        if overrides:
            payload["overrides"] = overrides
        if metadata:
            payload["metadata"] = metadata
        return await self._request("POST", "/tasks", json=payload)

    async def session(self, session_id: str) -> dict[str, Any]:
        """One session."""
        return await self._request("GET", f"/sessions/{session_id}")

    async def sessions(self) -> list[dict[str, Any]]:
        """Every session."""
        body = await self._request("GET", "/sessions")
        if isinstance(body, dict):
            return list(body.get("results") or [])
        return list(body or [])

    async def agents(self) -> list[dict[str, Any]]:
        """The agent registry the service is running from."""
        body = await self._request("GET", "/agents")
        if isinstance(body, dict):
            return list(body.get("results") or [])
        return list(body or [])

    async def agent(self, handle: str) -> dict[str, Any]:
        """One agent, with the models it names resolved."""
        return await self._request("GET", f"/agents/{handle}")
