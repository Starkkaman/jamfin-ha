"""A fake Jamfin TV server implementing docs/PROTOCOL.md for the tests.

It is deliberately small but follows the contract: pairing (PIN, 3 tries, one pairing at a
time, lockout after 3 failed pairings, 5 clients, replace by instance), Bearer checks before
the WebSocket upgrade, hello + state snapshot, JSON ping/pong, pushes, command results, close
codes, the art proxy, and a stand-in for Jellyfin's image endpoint ("direct" URLs).
"""

from __future__ import annotations

import asyncio
import copy
import itertools
import json
from pathlib import Path
import secrets
import time
from typing import Any

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer

FIXTURES = Path(__file__).parent / "fixtures"
JELLYFIN_IN_FIXTURES = "http://192.168.10.157:8096"
PNG = b"\x89PNG\r\n\x1a\n" + b"fake-proxied-art"
JPEG = b"\xff\xd8\xff\xe0" + b"fake-direct-art"
DEVICE_ID = "6f1d0c52-6b3e-4f6e-9a54-2d1f0a7c9e11"


def load_json(name: str) -> dict[str, Any]:
    """Load a fixture file."""
    return json.loads((FIXTURES / name).read_text())


def _error(status: int, code: str, message: str = "", **extra: Any) -> web.Response:
    return web.json_response(
        {"error": {"code": code, "message": message or code, **extra}}, status=status
    )


class FakeJamfin:
    """State and behaviour of the fake TV."""

    def __init__(self) -> None:
        self.hello = load_json("hello.json")
        self.device = dict(self.hello["device"])
        self.pin = "042917"
        self.foreground = True
        self.max_clients = 5
        self.clients: dict[str, dict[str, Any]] = {}  # key -> {client_id, instance, name}
        self.pairing: dict[str, Any] | None = None
        self.ended_pairings: set[str] = set()
        self.failed_pairings = 0
        self.lockout_until = 0.0
        self.state: dict[str, Any] = {}
        self.answer_pings = True
        self.reject_ws_status: int | None = None
        self.command_results: dict[str, dict[str, Any] | None] = {}
        self.commands: list[dict[str, Any]] = []
        self.pings = 0
        self.ws_connects = 0
        self.art_requests: list[str] = []
        self.jellyfin_requests: list[str] = []
        self.cancelled: list[str] = []
        self.unpaired: list[str] = []
        self.sockets: dict[web.WebSocketResponse, dict[str, Any]] = {}
        self._ids = itertools.count(1)
        self.server: TestServer | None = None

    # ------------------------------------------------------------------ helpers

    @property
    def port(self) -> int:
        """Bound port."""
        assert self.server is not None
        return self.server.port  # type: ignore[return-value]

    @property
    def jellyfin_base(self) -> str:
        """Where the fake 'Jellyfin' image endpoint lives."""
        return f"http://127.0.0.1:{self.port}/jellyfin"

    def fixture_state(self, name: str, *, jellyfin: str | None = None) -> dict[str, Any]:
        """Load a state fixture with the Jellyfin URLs pointed at ``jellyfin``."""
        text = (
            (FIXTURES / name)
            .read_text()
            .replace(JELLYFIN_IN_FIXTURES, jellyfin or self.jellyfin_base)
        )
        return json.loads(text)

    def add_client(self, key: str, instance: str = "test-instance") -> str:
        """Pre-pair a client key."""
        client_id = f"c_{next(self._ids)}"
        self.clients[key] = {"client_id": client_id, "instance": instance, "name": "HA"}
        return client_id

    def app(self) -> web.Application:
        """The aiohttp application."""
        app = web.Application()
        app.router.add_get("/api/v1/info", self._info)
        app.router.add_post("/api/v1/pair/start", self._pair_start)
        app.router.add_post("/api/v1/pair/confirm", self._pair_confirm)
        app.router.add_post("/api/v1/pair/cancel", self._pair_cancel)
        app.router.add_delete("/api/v1/pair", self._unpair)
        app.router.add_get("/api/v1/state", self._get_state)
        app.router.add_get("/api/v1/art/{kind}/{item_id}", self._art)
        app.router.add_get("/api/v1/ws", self._ws)
        app.router.add_get("/jellyfin/Items/{item_id}/Images/{rest:.*}", self._jellyfin_image)
        return app

    async def start(self) -> None:
        """Start on 127.0.0.1 with a free port."""
        self.server = TestServer(self.app(), host="127.0.0.1")
        await self.server.start_server()
        self.state = self.fixture_state("state_episode.json")

    async def close(self) -> None:
        """Stop."""
        for ws in list(self.sockets):
            await ws.close()
        if self.server is not None:
            await self.server.close()

    def _key(self, request: web.Request) -> str | None:
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            key = header.removeprefix("Bearer ")
            if key in self.clients:
                return key
        return None

    # ------------------------------------------------------------------ REST

    async def _info(self, request: web.Request) -> web.Response:
        pairing = "available"
        if time.monotonic() < self.lockout_until:
            pairing = "locked"
        elif self.pairing is not None:
            pairing = "in_progress"
        elif not self.foreground:
            pairing = "needs_foreground"
        return web.json_response(
            {
                **self.device,
                "api": 1,
                "api_versions": [1],
                "pairing": pairing,
            }
        )

    async def _pair_start(self, request: web.Request) -> web.Response:
        body = await request.json()
        if time.monotonic() < self.lockout_until:
            return _error(429, "rate_limited", retry_after=900)
        if not self.foreground:
            return _error(409, "not_in_foreground", "Open Jamfin on the TV")
        if self.pairing is not None:
            return _error(409, "pairing_in_progress", retry_after=97)
        instance = body.get("client_instance")
        known = any(c["instance"] == instance for c in self.clients.values())
        if len(self.clients) >= self.max_clients and not known:
            return _error(409, "too_many_clients")
        pairing_id = f"p_{next(self._ids)}"
        self.pairing = {
            "pairing_id": pairing_id,
            "attempts": 3,
            "instance": instance,
            "name": body.get("client_name"),
        }
        return web.json_response(
            {"pairing_id": pairing_id, "expires_in": 120, "pin_length": 6, "attempts": 3},
            status=202,
        )

    def _end_pairing(self) -> None:
        if self.pairing is not None:
            self.ended_pairings.add(self.pairing["pairing_id"])
        self.pairing = None

    async def _pair_confirm(self, request: web.Request) -> web.Response:
        body = await request.json()
        if time.monotonic() < self.lockout_until:
            return _error(429, "rate_limited", retry_after=900)
        pairing_id = body.get("pairing_id")
        if self.pairing is None or self.pairing["pairing_id"] != pairing_id:
            if pairing_id in self.ended_pairings:
                return _error(410, "pairing_expired")
            return _error(404, "not_found")
        if body.get("pin") != self.pin:
            self.pairing["attempts"] -= 1
            left = self.pairing["attempts"]
            if left == 0:
                self._end_pairing()
                self.failed_pairings += 1
                if self.failed_pairings >= 3:
                    self.lockout_until = time.monotonic() + 900
            return _error(401, "invalid_pin", f"Wrong PIN. {left} tries left.", attempts_left=left)
        instance = self.pairing["instance"]
        for key, client in list(self.clients.items()):
            if client["instance"] == instance:
                del self.clients[key]
        key = secrets.token_urlsafe(32)
        client_id = self.add_client(key, instance)
        self._end_pairing()
        return web.json_response(
            {"client_id": client_id, "client_key": key, "device": self.device, "api": 1}
        )

    async def _pair_cancel(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.cancelled.append(body.get("pairing_id"))
        if self.pairing is not None and self.pairing["pairing_id"] == body.get("pairing_id"):
            self._end_pairing()
        return web.Response(status=204)

    async def _unpair(self, request: web.Request) -> web.Response:
        if (key := self._key(request)) is None:
            return _error(401, "unauthorized")
        self.unpaired.append(self.clients.pop(key)["client_id"])
        for ws, info in list(self.sockets.items()):
            if info["key"] == key:
                await ws.close(code=4401)
        return web.Response(status=204)

    async def _get_state(self, request: web.Request) -> web.Response:
        if self._key(request) is None:
            return _error(401, "unauthorized")
        return web.json_response({**self.state, "seq": None})

    async def _art(self, request: web.Request) -> web.Response:
        if self._key(request) is None:
            return _error(401, "unauthorized")
        self.art_requests.append(request.path_qs)
        images = (self.state.get("media") or {}).get("images") or {}
        if not any(ref.get("proxy") == request.path_qs for ref in images.values()):
            return _error(404, "not_found")
        return web.Response(body=PNG, content_type="image/png")

    async def _jellyfin_image(self, request: web.Request) -> web.Response:
        self.jellyfin_requests.append(request.path_qs)
        return web.Response(body=JPEG, content_type="image/jpeg")

    # ------------------------------------------------------------------ WebSocket

    async def _ws(self, request: web.Request) -> web.StreamResponse:
        if self.reject_ws_status is not None:
            return _error(self.reject_ws_status, "rate_limited")
        if (key := self._key(request)) is None:
            return _error(401, "unauthorized")
        ws = web.WebSocketResponse(autoping=True)
        await ws.prepare(request)
        for other, info in list(self.sockets.items()):
            if info["key"] == key:
                await other.close(code=4000)
        self.sockets[ws] = {"key": key, "seq": 0}
        self.ws_connects += 1
        hello = copy.deepcopy(self.hello)
        hello["client_id"] = self.clients[key]["client_id"]
        hello["server_time"] = _now()
        await ws.send_json(hello)
        await self._send_state(ws)
        try:
            async for msg in ws:
                if msg.type is not WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                if data.get("type") == "ping":
                    self.pings += 1
                    if self.answer_pings:
                        await ws.send_json(
                            {"type": "pong", "id": data.get("id"), "server_time": _now()}
                        )
                elif data.get("type") == "command":
                    await self._command(ws, data)
        finally:
            self.sockets.pop(ws, None)
        return ws

    async def _send_state(self, ws: web.WebSocketResponse) -> None:
        info = self.sockets[ws]
        info["seq"] += 1
        await ws.send_json({"type": "state", "seq": info["seq"], "state": self.state})

    async def _command(self, ws: web.WebSocketResponse, msg: dict[str, Any]) -> None:
        self.commands.append(msg)
        name = msg.get("command")
        result: dict[str, Any]
        if name in self.command_results:
            if self.command_results[name] is None:
                return  # never answer (client timeout)
            result = self.command_results[name]
        elif name in ("play_media", "go_home") and (
            (self.state.get("app") or {}).get("state") == "background"
        ):
            # Foreground rule (PROTOCOL.md 9.3, Q4)
            result = {
                "ok": False,
                "error": {"code": "not_in_foreground", "message": "Jamfin is not on screen."},
            }
        else:
            result = {"ok": True, "data": {}}
        await ws.send_json(
            {
                "type": "result",
                "id": msg.get("id"),
                "state_seq": self.sockets[ws]["seq"],
                **result,
            }
        )

    # ------------------------------------------------------------------ pushes

    async def push_state(self, state: dict[str, Any] | None = None) -> None:
        """Push a (new) full state to every socket."""
        if state is not None:
            self.state = state
        for ws in list(self.sockets):
            await self._send_state(ws)

    async def push_event(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Push an event to every socket."""
        for ws, info in list(self.sockets.items()):
            await ws.send_json(
                {
                    "type": "event",
                    "event": event,
                    "at": _now(),
                    "state_seq": info["seq"],
                    "data": data or {},
                }
            )

    async def close_sockets(self, code: int) -> None:
        """Close every socket with ``code``."""
        for ws in list(self.sockets):
            await ws.close(code=code)

    async def wait_for_socket(self, count: int = 1, timeout: float = 5) -> None:
        """Wait until ``count`` WebSocket connections were made in total."""
        async with asyncio.timeout(timeout):
            while self.ws_connects < count or not self.sockets:
                await asyncio.sleep(0.01)


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
