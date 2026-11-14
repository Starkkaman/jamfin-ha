"""Push client: one WebSocket per TV (docs/PROTOCOL.md sections 6 and 10)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import contextlib
from datetime import timedelta
import itertools
import json
import random
import time
from typing import TYPE_CHECKING, Any

import aiohttp

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .api import (
    JamfinApi,
    JamfinAuthError,
    JamfinCommandError,
    JamfinConnectionError,
    JamfinError,
    JamfinNotConnectedError,
    fetch_image,
)
from .const import (
    BACKOFF_JITTER,
    BACKOFF_RESET_AFTER,
    BACKOFF_STEPS,
    CLOCK_SKEW_IGNORE,
    COMMAND_TIMEOUT,
    CONNECT_TIMEOUT,
    DIRECT_ART_RETRY_AFTER,
    DIRECT_ART_TIMEOUT,
    HELLO_TIMEOUT,
    LOGGER,
    REPLACED_MAX_IN_A_ROW,
    REPLACED_RETRY_DELAY,
    RESOLVE_AFTER_FAILURES,
    SUPPORTED_API_VERSIONS,
)
from .models import Capabilities, Hello, ImageRef, JamfinState, parse_time

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.config_entries import ConfigEntry

type Resolver = Callable[[], Awaitable[tuple[str, int] | None]]
type StateListener = Callable[[JamfinState | None, JamfinState], None]
type EventListener = Callable[[str, dict[str, Any]], None]
type ConnectionListener = Callable[[bool], None]

_WS_CLOSE_TIMEOUT = 2.0
_MAX_MESSAGE_BYTES = 1024 * 1024


class _ReplacedError(Exception):
    """The server closed this socket with 4000 (a newer socket uses the key)."""


class _PongTimeoutError(Exception):
    """No pong within the heartbeat's pong_timeout."""


def backoff_delay(attempt: int, rng: Callable[[], float] = random.random) -> float:
    """Delay before reconnect attempt ``attempt`` (0-based), with +-20 % jitter."""
    base = BACKOFF_STEPS[min(attempt, len(BACKOFF_STEPS) - 1)]
    return base * (1 + BACKOFF_JITTER * (2 * rng() - 1))


def _add_listener[T](listeners: list[T], listener: T) -> CALLBACK_TYPE:
    listeners.append(listener)

    @callback
    def _remove() -> None:
        with contextlib.suppress(ValueError):
            listeners.remove(listener)

    return _remove


class JamfinClient:
    """Keeps one authenticated WebSocket to one TV and exposes its state."""

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        *,
        host: str,
        port: int,
        key: str,
        jamfin_id: str,
        name: str,
        capabilities: Capabilities | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        """Initialise."""
        self.hass = hass
        self._session = session
        self._key = key
        self.api = JamfinApi(session, host, port, key)
        self.jamfin_id = jamfin_id
        self.name = name
        self.capabilities = capabilities
        self.hello: Hello | None = None
        self.state: JamfinState | None = None
        self.connected = False
        self.auth_failed = False
        self.replaced = False
        self.skew = timedelta(0)
        self.last_seq: int | None = None
        self.ha_device_id: str | None = None
        self.stats: dict[str, Any] = {
            "connects": 0,
            "disconnects": 0,
            "consecutive_failures": 0,
            "last_close_code": None,
            "last_error": None,
            "next_retry_in": None,
            "connected_since": None,
        }
        self.on_auth_failed: Callable[[], None] | None = None
        self.on_hello: Callable[[Hello], None] | None = None
        self.on_address_changed: Callable[[str, int], None] | None = None
        self._resolver = resolver
        self._listeners: list[CALLBACK_TYPE] = []
        self._state_listeners: list[StateListener] = []
        self._event_listeners: list[EventListener] = []
        self._capability_listeners: list[CALLBACK_TYPE] = []
        self._connection_listeners: list[ConnectionListener] = []
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._pending: dict[int, asyncio.Future[Mapping[str, Any]]] = {}
        self._ids = itertools.count(1)
        self._wake = asyncio.Event()
        self._first_attempt = asyncio.Event()
        self._stopping = False
        self._task: asyncio.Task[None] | None = None
        self._connected_at: float | None = None
        self._pong_deadline: float | None = None
        self._outage_logged = False
        self._direct_art_blocked_until = 0.0

    # ------------------------------------------------------------------ listeners

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call ``listener()`` on every state or availability change."""
        return _add_listener(self._listeners, listener)

    @callback
    def async_add_state_listener(self, listener: StateListener) -> CALLBACK_TYPE:
        """Call ``listener(previous, new)`` on every state push.

        ``previous`` is ``None`` for the snapshot after a (re)connect.
        """
        return _add_listener(self._state_listeners, listener)

    @callback
    def async_add_event_listener(self, listener: EventListener) -> CALLBACK_TYPE:
        """Call ``listener(event, data)`` for every protocol event."""
        return _add_listener(self._event_listeners, listener)

    @callback
    def async_add_capabilities_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call ``listener()`` when ``hello.capabilities`` changed."""
        return _add_listener(self._capability_listeners, listener)

    @callback
    def async_add_connection_listener(self, listener: ConnectionListener) -> CALLBACK_TYPE:
        """Call ``listener(connected)`` when the socket comes up or goes down."""
        return _add_listener(self._connection_listeners, listener)

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    # ------------------------------------------------------------------ lifecycle

    @callback
    def async_start(self, entry: ConfigEntry) -> None:
        """Start the connection loop as a background task of the entry."""
        self._stopping = False
        self._task = entry.async_create_background_task(
            self.hass, self._run(), f"jamfin connection {self.name}"
        )

    async def async_wait_first_attempt(self, timeout: float) -> None:
        """Wait until the first connect attempt succeeded or failed (max ``timeout``)."""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(timeout):
                await self._first_attempt.wait()

    async def async_stop(self) -> None:
        """Close the socket and stop reconnecting."""
        self._stopping = True
        self._wake.set()
        ws = self._ws
        if ws is not None and not ws.closed:
            with contextlib.suppress(Exception):
                await ws.close()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self._set_disconnected()

    @callback
    def async_update_address(self, host: str, port: int) -> None:
        """Use a new address (zeroconf saw the TV) and retry now if disconnected."""
        if (host, port) != (self.api.host, self.api.port):
            LOGGER.info("%s: address changed to %s:%s", self.name, host, port)
            self.api.host = host
            self.api.port = port
        if not self.connected:
            self._wake.set()

    # ------------------------------------------------------------------ connection loop

    async def _run(self) -> None:
        failures = 0
        replaced_in_a_row = 0
        while not self._stopping:
            self._wake.clear()
            self._connected_at = None
            error: str | None = None
            try:
                await self._connect_and_listen()
            except JamfinAuthError:
                self._finish_attempt()
                LOGGER.warning(
                    "%s: the TV refused this Home Assistant's key; pair again", self.name
                )
                self.auth_failed = True
                if self.on_auth_failed is not None:
                    self.on_auth_failed()
                return
            except _ReplacedError:
                stayed_up = (
                    self._connected_at is not None
                    and time.monotonic() - self._connected_at >= BACKOFF_RESET_AFTER
                )
                self._finish_attempt()
                replaced_in_a_row = 1 if stayed_up else replaced_in_a_row + 1
                if replaced_in_a_row >= REPLACED_MAX_IN_A_ROW:
                    self.replaced = True
                    LOGGER.warning(
                        "%s: another connection with the same key keeps replacing this one; "
                        "not reconnecting",
                        self.name,
                    )
                    return
                LOGGER.info(
                    "%s: the TV closed this socket as replaced (4000); retrying in %.0f s",
                    self.name,
                    REPLACED_RETRY_DELAY,
                )
                self.stats["next_retry_in"] = REPLACED_RETRY_DELAY
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(REPLACED_RETRY_DELAY):
                        await self._wake.wait()
                continue
            except (
                JamfinError,
                aiohttp.ClientError,
                TimeoutError,
                OSError,
                ValueError,
                _PongTimeoutError,
            ) as err:
                error = str(err) or type(err).__name__
            was_up = self._connected_at is not None
            up_for = time.monotonic() - self._connected_at if was_up else 0.0
            self._finish_attempt()
            if self._stopping:
                return
            if up_for >= BACKOFF_RESET_AFTER:
                failures = 0
            self.stats["last_error"] = error
            if not self._outage_logged:
                self._outage_logged = True
                if was_up:
                    LOGGER.info(
                        "%s: connection lost (%s); reconnecting", self.name, error or "closed"
                    )
                else:
                    LOGGER.info("%s: not reachable (%s); will keep trying", self.name, error)
            delay = backoff_delay(failures)
            failures += 1
            self.stats["consecutive_failures"] = failures
            self.stats["next_retry_in"] = round(delay, 2)
            if failures >= RESOLVE_AFTER_FAILURES:
                await self._async_resolve()
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(delay):
                    await self._wake.wait()

    @callback
    def _finish_attempt(self) -> None:
        self._set_disconnected()
        self._first_attempt.set()

    async def _async_resolve(self) -> None:
        if self._resolver is None:
            return
        try:
            address = await self._resolver()
        except Exception:
            LOGGER.debug("%s: zeroconf re-resolve failed", self.name, exc_info=True)
            return
        if address is None or address == (self.api.host, self.api.port):
            return
        host, port = address
        LOGGER.info("%s: zeroconf says the TV is now at %s:%s", self.name, host, port)
        self.api.host = host
        self.api.port = port
        if self.on_address_changed is not None:
            self.on_address_changed(host, port)

    async def _connect_and_listen(self) -> None:
        url = f"{self.api.base}/api/v1/ws"
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                ws = await self._session.ws_connect(
                    url,
                    headers={"Authorization": f"Bearer {self._key}"},
                    autoping=True,
                    heartbeat=None,
                    timeout=aiohttp.ClientWSTimeout(ws_receive=None, ws_close=_WS_CLOSE_TIMEOUT),
                    max_msg_size=_MAX_MESSAGE_BYTES,
                )
        except aiohttp.WSServerHandshakeError as err:
            if err.status == 401:
                raise JamfinAuthError("unauthorized", status=401) from err
            raise JamfinConnectionError(f"WebSocket upgrade refused (HTTP {err.status})") from err
        self._ws = ws
        try:
            msg = await ws.receive(timeout=HELLO_TIMEOUT)
            hello = self._decode(ws, msg)
            if hello is None or hello.get("type") != "hello":
                raise JamfinConnectionError("The server did not say hello")
            self._handle_hello(hello)
            await self._listen(ws)
        finally:
            self._ws = None
            if not ws.closed:
                with contextlib.suppress(Exception):
                    await ws.close()

    def _decode(
        self, ws: aiohttp.ClientWebSocketResponse, msg: aiohttp.WSMessage
    ) -> dict[str, Any] | None:
        """Return the JSON object of a text frame; handle close frames."""
        if msg.type is aiohttp.WSMsgType.TEXT:
            try:
                data = json.loads(msg.data)
            except ValueError:
                LOGGER.debug("%s: invalid JSON frame ignored", self.name)
                return None
            return data if isinstance(data, dict) else None
        if msg.type in (
            aiohttp.WSMsgType.CLOSE,
            aiohttp.WSMsgType.CLOSING,
            aiohttp.WSMsgType.CLOSED,
            aiohttp.WSMsgType.ERROR,
        ):
            code = msg.data if msg.type is aiohttp.WSMsgType.CLOSE else ws.close_code
            self.stats["last_close_code"] = code
            if code == 4401:
                raise JamfinAuthError("unauthorized", "Key revoked (4401)")
            if code == 4000:
                raise _ReplacedError
            raise JamfinConnectionError(f"Socket closed ({code})")
        return None

    async def _listen(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        loop = asyncio.get_running_loop()
        assert self.hello is not None
        interval = self.hello.ping_interval
        pong_timeout = self.hello.pong_timeout
        next_ping = loop.time() + interval
        self._pong_deadline = None
        while True:
            deadline = next_ping
            if self._pong_deadline is not None:
                deadline = min(deadline, self._pong_deadline)
            try:
                msg = await ws.receive(timeout=max(deadline - loop.time(), 0.001))
            except TimeoutError:
                now = loop.time()
                if self._pong_deadline is not None and now >= self._pong_deadline:
                    raise _PongTimeoutError(f"no pong within {pong_timeout} s") from None
                if now >= next_ping:
                    await ws.send_json({"type": "ping", "id": next(self._ids)})
                    if self._pong_deadline is None:
                        self._pong_deadline = now + pong_timeout
                    next_ping = now + interval
                continue
            if (data := self._decode(ws, msg)) is not None:
                self._dispatch(data)

    # ------------------------------------------------------------------ messages

    @callback
    def _dispatch(self, msg: Mapping[str, Any]) -> None:
        match msg.get("type"):
            case "state":
                self._handle_state(msg)
            case "event":
                self._handle_event(msg)
            case "result":
                self._handle_result(msg)
            case "pong":
                self._pong_deadline = None
                self._update_skew(msg.get("server_time"))
            case "hello":
                self._handle_hello(msg)
            case other:
                LOGGER.debug("%s: ignoring message type %r", self.name, other)

    def _update_skew(self, server_time: Any) -> None:
        tv_time: datetime | None = parse_time(server_time)
        if tv_time is None:
            return
        skew = dt_util.utcnow() - tv_time
        self.skew = skew if abs(skew.total_seconds()) >= CLOCK_SKEW_IGNORE else timedelta(0)

    @callback
    def _handle_hello(self, msg: Mapping[str, Any]) -> None:
        hello = Hello.from_dict(msg)
        self.hello = hello
        self.last_seq = None
        self._update_skew(msg.get("server_time"))
        if hello.api is not None and hello.api not in SUPPORTED_API_VERSIONS:
            LOGGER.warning(
                "%s: the TV speaks API %s, this integration speaks %s",
                self.name,
                hello.api,
                sorted(SUPPORTED_API_VERSIONS),
            )
        if hello.capabilities != self.capabilities:
            self.capabilities = hello.capabilities
            for listener in list(self._capability_listeners):
                listener()
        if self.on_hello is not None:
            self.on_hello(hello)

    @callback
    def _handle_state(self, msg: Mapping[str, Any]) -> None:
        raw = msg.get("state")
        if not isinstance(raw, Mapping):
            return
        seq = msg.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool):
            if self.last_seq is not None and seq != self.last_seq + 1:
                LOGGER.debug("%s: state seq jumped from %s to %s", self.name, self.last_seq, seq)
            self.last_seq = seq
        new = JamfinState.from_dict(raw)
        previous = self.state if self.connected else None
        self.state = new
        if not self.connected:
            self._set_connected()
        for listener in list(self._state_listeners):
            listener(previous, new)
        self._notify()

    @callback
    def _handle_event(self, msg: Mapping[str, Any]) -> None:
        name = msg.get("event")
        if not isinstance(name, str):
            return
        data = msg.get("data")
        payload = dict(data) if isinstance(data, Mapping) else {}
        for listener in list(self._event_listeners):
            listener(name, payload)

    @callback
    def _handle_result(self, msg: Mapping[str, Any]) -> None:
        future = self._pending.get(msg.get("id"))  # type: ignore[arg-type]
        if future is not None and not future.done():
            future.set_result(msg)

    @callback
    def _set_connected(self) -> None:
        self.connected = True
        self._connected_at = time.monotonic()
        self.stats["connects"] += 1
        self.stats["consecutive_failures"] = 0
        self.stats["next_retry_in"] = None
        self.stats["connected_since"] = dt_util.utcnow().isoformat()
        self._first_attempt.set()
        if self._outage_logged:
            LOGGER.info("%s: connected again", self.name)
            self._outage_logged = False
        for listener in list(self._connection_listeners):
            listener(True)

    @callback
    def _set_disconnected(self) -> None:
        self._ws = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(JamfinNotConnectedError("connection lost"))
        self._pending.clear()
        if not self.connected:
            return
        self.connected = False
        self.stats["disconnects"] += 1
        self.stats["connected_since"] = None
        if self._stopping:
            return
        for listener in list(self._connection_listeners):
            listener(False)
        self._notify()

    # ------------------------------------------------------------------ commands

    async def async_command(
        self,
        command: str,
        args: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a command over the socket and wait for its result (section 9)."""
        ws = self._ws
        if not self.connected or ws is None or ws.closed:
            raise JamfinNotConnectedError("not connected")
        if self.capabilities is not None and command not in self.capabilities.commands:
            raise JamfinCommandError("unknown_command", f"The TV does not offer {command}")
        command_id = next(self._ids)
        message: dict[str, Any] = {"type": "command", "id": command_id, "command": command}
        if args:
            message["args"] = dict(args)
        future: asyncio.Future[Mapping[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[command_id] = future
        try:
            await ws.send_json(message)
            async with asyncio.timeout(timeout or COMMAND_TIMEOUT):
                result = await future
        except TimeoutError as err:
            raise JamfinCommandError("timeout", "No answer from the TV") from err
        except (aiohttp.ClientError, ConnectionError) as err:
            raise JamfinNotConnectedError(str(err)) from err
        finally:
            self._pending.pop(command_id, None)
        if result.get("ok") is True:
            data = result.get("data")
            return dict(data) if isinstance(data, Mapping) else {}
        error = result.get("error")
        error = error if isinstance(error, Mapping) else {}
        code = error.get("code") if isinstance(error.get("code"), str) else "internal"
        message_text = error.get("message")
        raise JamfinCommandError(code, message_text if isinstance(message_text, str) else None)

    # ------------------------------------------------------------------ art

    async def async_fetch_image(self, ref: ImageRef) -> tuple[bytes, str] | None:
        """Fetch art: the direct Jellyfin URL first, the TV's proxy as fallback."""
        if ref.url and time.monotonic() >= self._direct_art_blocked_until:
            try:
                return await fetch_image(self._session, ref.url, timeout=DIRECT_ART_TIMEOUT)
            except JamfinError as err:
                LOGGER.debug("%s: direct art URL failed (%s), using the TV's proxy", self.name, err)
                if ref.proxy:
                    self._direct_art_blocked_until = time.monotonic() + DIRECT_ART_RETRY_AFTER
        if ref.proxy:
            try:
                return await self.api.async_get_art(ref.proxy)
            except JamfinError as err:
                LOGGER.debug("%s: art proxy failed: %s", self.name, err)
        return None

    # ------------------------------------------------------------------ diagnostics

    def diagnostics(self) -> dict[str, Any]:
        """Return connection details for diagnostics."""
        return {
            "connected": self.connected,
            "auth_failed": self.auth_failed,
            "replaced": self.replaced,
            "last_seq": self.last_seq,
            "clock_skew_seconds": self.skew.total_seconds(),
            "capabilities": self.capabilities.as_dict() if self.capabilities else None,
            "stats": dict(self.stats),
        }
