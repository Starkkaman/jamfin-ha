"""REST side of the Jamfin local API v1 (docs/PROTOCOL.md section 5).

Plain aiohttp, no Home Assistant imports, so the config flow and the client share it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import aiohttp
from yarl import URL

from .const import ART_MAX_BYTES, REST_TIMEOUT


class JamfinError(Exception):
    """Base error."""


class JamfinConnectionError(JamfinError):
    """The TV could not be reached or answered garbage."""


class JamfinNotConnectedError(JamfinError):
    """The WebSocket is down, so a command can't be sent."""


class JamfinApiError(JamfinError):
    """The server answered with a protocol error object (section 3.1)."""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        status: int | None = None,
        attempts_left: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Initialise."""
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message
        self.status = status
        self.attempts_left = attempts_left
        self.retry_after = retry_after


class JamfinAuthError(JamfinApiError):
    """The client key was refused (401 / close 4401)."""


class JamfinCommandError(JamfinError):
    """A command failed (section 9.2)."""

    def __init__(self, code: str, message: str | None = None) -> None:
        """Initialise."""
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message


def base_url(host: str, port: int) -> str:
    """``http://host:port`` (IPv6 literals bracketed)."""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}"


def _error_from_body(status: int, body: Any) -> JamfinApiError:
    error = body.get("error") if isinstance(body, Mapping) else None
    if not isinstance(error, Mapping):
        error = {}
    code = error.get("code") if isinstance(error.get("code"), str) else None
    if code is None:
        code = "unauthorized" if status == 401 else f"http_{status}"
    attempts = error.get("attempts_left")
    retry = error.get("retry_after")
    cls = JamfinAuthError if code == "unauthorized" else JamfinApiError
    return cls(
        code,
        error.get("message") if isinstance(error.get("message"), str) else None,
        status=status,
        attempts_left=attempts if isinstance(attempts, int) else None,
        retry_after=float(retry) if isinstance(retry, (int, float)) else None,
    )


class JamfinApi:
    """REST calls against one TV."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        key: str | None = None,
    ) -> None:
        """Initialise."""
        self._session = session
        self.host = host
        self.port = port
        self._key = key

    @property
    def base(self) -> str:
        """Base URL of the server."""
        return base_url(self.host, self.port)

    def _headers(self, auth: bool) -> dict[str, str]:
        if auth:
            if not self._key:
                raise JamfinAuthError("unauthorized", "No client key")
            return {"Authorization": f"Bearer {self._key}"}
        return {}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        auth: bool = False,
        json: Mapping[str, Any] | None = None,
    ) -> Any:
        try:
            async with self._session.request(
                method,
                f"{self.base}{path}",
                headers=self._headers(auth),
                json=json,
                timeout=aiohttp.ClientTimeout(total=REST_TIMEOUT),
            ) as resp:
                if resp.status == 204:
                    return None
                try:
                    body = await resp.json(content_type=None)
                except ValueError:
                    body = None
                if resp.status >= 400:
                    raise _error_from_body(resp.status, body)
                if body is None:
                    raise JamfinConnectionError(f"Invalid JSON from {path}")
                return body
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise JamfinConnectionError(str(err) or type(err).__name__) from err

    async def async_get_info(self) -> dict[str, Any]:
        """``GET /api/v1/info``."""
        body = await self._request("GET", "/api/v1/info")
        if not isinstance(body, Mapping) or not isinstance(body.get("id"), str):
            raise JamfinConnectionError("Not a Jamfin server")
        return dict(body)

    async def async_pair_start(self, client_name: str, client_instance: str) -> dict[str, Any]:
        """``POST /api/v1/pair/start``."""
        body = await self._request(
            "POST",
            "/api/v1/pair/start",
            json={"client_name": client_name, "client_instance": client_instance},
        )
        if not isinstance(body, Mapping) or not isinstance(body.get("pairing_id"), str):
            raise JamfinConnectionError("Invalid pair/start answer")
        return dict(body)

    async def async_pair_confirm(self, pairing_id: str, pin: str) -> dict[str, Any]:
        """``POST /api/v1/pair/confirm``."""
        body = await self._request(
            "POST",
            "/api/v1/pair/confirm",
            json={"pairing_id": pairing_id, "pin": pin},
        )
        if not isinstance(body, Mapping) or not isinstance(body.get("client_key"), str):
            raise JamfinConnectionError("Invalid pair/confirm answer")
        return dict(body)

    async def async_pair_cancel(self, pairing_id: str) -> None:
        """``POST /api/v1/pair/cancel``."""
        await self._request("POST", "/api/v1/pair/cancel", json={"pairing_id": pairing_id})

    async def async_unpair(self) -> None:
        """``DELETE /api/v1/pair``."""
        await self._request("DELETE", "/api/v1/pair", auth=True)

    async def async_get_state(self) -> dict[str, Any]:
        """``GET /api/v1/state``."""
        return dict(await self._request("GET", "/api/v1/state", auth=True))

    async def async_get_art(self, proxy: str) -> tuple[bytes, str]:
        """Fetch an image through the TV's art proxy (section 7.4).

        ``proxy`` is used exactly as the server gave it, and only as a path on this
        server, so the key never goes to another host.
        """
        if not proxy.startswith("/") or proxy.startswith("//"):
            raise JamfinConnectionError("Proxy path is not a path on the TV")
        url = URL(f"{self.base}{proxy}", encoded=True)
        return await fetch_image(self._session, url, headers=self._headers(True))


async def fetch_image(
    session: aiohttp.ClientSession,
    url: str | URL,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = REST_TIMEOUT,
) -> tuple[bytes, str]:
    """GET an image (max 5 MB); raise ``JamfinConnectionError`` on any failure."""
    try:
        async with session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status == 401:
                raise JamfinAuthError("unauthorized", status=401)
            if resp.status != 200:
                raise JamfinConnectionError(f"HTTP {resp.status}")
            content_type = resp.headers.get("Content-Type", "").split(";")[0].strip()
            if not content_type.startswith("image/"):
                raise JamfinConnectionError(f"Not an image: {content_type!r}")
            if (resp.content_length or 0) > ART_MAX_BYTES:
                raise JamfinConnectionError("Image too large")
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.content.iter_chunked(65536):
                total += len(chunk)
                if total > ART_MAX_BYTES:
                    raise JamfinConnectionError("Image too large")
                chunks.append(chunk)
            return b"".join(chunks), content_type
    except (aiohttp.ClientError, TimeoutError, OSError, asyncio.IncompleteReadError) as err:
        raise JamfinConnectionError(str(err) or type(err).__name__) from err
