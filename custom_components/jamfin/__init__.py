"""The Jamfin integration: local push from Jamfin TV (docs/PROTOCOL.md)."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import JamfinApi, JamfinError
from .client import JamfinClient
from .const import (
    CONF_APP_VERSION,
    CONF_CAPABILITIES,
    CONF_CLIENT_KEY,
    CONF_JAMFIN_ID,
    CONF_MODEL,
    CONF_ZEROCONF_NAME,
    DOMAIN,
    EVENT_JAMFIN,
    LOGGER,
    PROTOCOL_EVENTS,
    SETUP_FIRST_CONNECT_WAIT,
    ZEROCONF_TYPE,
)
from .models import Capabilities, Hello, JamfinState
from .services import async_setup_services
from .triggers import derive_transitions

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.IMAGE,
    Platform.MEDIA_PLAYER,
    Platform.SELECT,
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
MANUFACTURER = "Jamfin"

type JamfinConfigEntry = ConfigEntry[JamfinClient]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: JamfinConfigEntry) -> bool:
    """Connect to one TV."""
    data = entry.data
    jamfin_id: str = entry.unique_id or data[CONF_JAMFIN_ID]
    cached = data.get(CONF_CAPABILITIES)
    client = JamfinClient(
        hass,
        async_get_clientsession(hass),
        host=data[CONF_HOST],
        port=data[CONF_PORT],
        key=data[CONF_CLIENT_KEY],
        jamfin_id=jamfin_id,
        name=entry.title,
        capabilities=Capabilities.from_dict(cached) if cached is not None else None,
        resolver=partial(_async_resolve, hass, entry),
    )
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, jamfin_id)},
        manufacturer=MANUFACTURER,
        model=data.get(CONF_MODEL),
        name=entry.title,
        sw_version=data.get(CONF_APP_VERSION),
    )
    client.ha_device_id = device.id
    client.on_hello = partial(_async_on_hello, hass, entry, device.id)
    client.on_address_changed = partial(_async_on_address_changed, hass, entry)
    client.on_auth_failed = partial(entry.async_start_reauth, hass)

    client.async_start(entry)
    # Give a reachable TV a moment so entities start with real state. An unreachable
    # TV is not an error: it shows as off and the client keeps retrying.
    await client.async_wait_first_attempt(SETUP_FIRST_CONNECT_WAIT)
    if client.auth_failed:
        await client.async_stop()
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="auth_failed",
            translation_placeholders={"name": entry.title},
        )

    entry.runtime_data = client
    _async_setup_bus_events(hass, entry, client)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: JamfinConfigEntry) -> bool:
    """Disconnect from one TV."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_stop()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: JamfinConfigEntry) -> None:
    """Unpair from the TV (best effort; the TV may be off)."""
    api = JamfinApi(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_CLIENT_KEY],
    )
    try:
        async with asyncio.timeout(5):
            await api.async_unpair()
    except (JamfinError, TimeoutError) as err:
        LOGGER.debug("%s: could not unpair: %s", entry.title, err)


@callback
def _async_on_hello(
    hass: HomeAssistant, entry: JamfinConfigEntry, device_id: str, hello: Hello
) -> None:
    """Keep the device and the cached capabilities current."""
    device = hello.device
    dev_reg = dr.async_get(hass)
    if dev := dev_reg.async_get(device_id):
        dev_reg.async_update_device(
            dev.id,
            model=device.model or dev.model,
            sw_version=device.app_version or dev.sw_version,
        )
    updates: dict[str, Any] = {CONF_CAPABILITIES: hello.capabilities.as_dict()}
    if device.model:
        updates[CONF_MODEL] = device.model
    if device.app_version:
        updates[CONF_APP_VERSION] = device.app_version
    if any(entry.data.get(key) != value for key, value in updates.items()):
        hass.config_entries.async_update_entry(entry, data={**entry.data, **updates})


@callback
def _async_on_address_changed(
    hass: HomeAssistant, entry: JamfinConfigEntry, host: str, port: int
) -> None:
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_HOST: host, CONF_PORT: port}
    )


@callback
def _async_setup_bus_events(
    hass: HomeAssistant, entry: JamfinConfigEntry, client: JamfinClient
) -> None:
    """Fire ``jamfin_event`` for protocol moments and derived transitions."""

    @callback
    def _fire(event: str, data: dict[str, Any], **extra: Any) -> None:
        hass.bus.async_fire(
            EVENT_JAMFIN,
            {
                "device_id": client.ha_device_id,
                "jamfin_id": client.jamfin_id,
                "event": event,
                "data": data,
                **extra,
            },
        )

    @callback
    def _on_event(event: str, data: dict[str, Any]) -> None:
        if event not in PROTOCOL_EVENTS:
            LOGGER.debug("%s: ignoring unknown event %r", client.name, event)
            return
        extra: dict[str, Any] = {"derived": False}
        if event == "segment_started" and isinstance(data.get("kind"), str):
            extra["kind"] = data["kind"]
        _fire(event, data, **extra)

    @callback
    def _on_state(previous: JamfinState | None, new: JamfinState) -> None:
        for transition in derive_transitions(previous, new):
            _fire(transition, {"state": new.player_state}, derived=True)

    @callback
    def _on_connection(connected: bool) -> None:
        if not connected:
            _fire("turned_off", {}, derived=True)

    entry.async_on_unload(client.async_add_event_listener(_on_event))
    entry.async_on_unload(client.async_add_state_listener(_on_state))
    entry.async_on_unload(client.async_add_connection_listener(_on_connection))


async def _async_resolve(hass: HomeAssistant, entry: JamfinConfigEntry) -> tuple[str, int] | None:
    """Ask zeroconf where the TV is now (only for entries found by zeroconf)."""
    if not (name := entry.data.get(CONF_ZEROCONF_NAME)):
        return None
    from zeroconf.asyncio import AsyncServiceInfo  # noqa: PLC0415

    from homeassistant.components import zeroconf  # noqa: PLC0415

    aiozc = await zeroconf.async_get_async_instance(hass)
    info = AsyncServiceInfo(ZEROCONF_TYPE, name)
    if not await info.async_request(aiozc.zeroconf, 3000):
        return None
    props = {
        key.decode(): value.decode() if isinstance(value, bytes) else value
        for key, value in info.properties.items()
        if isinstance(key, bytes)
    }
    if props.get("id") != entry.unique_id or not info.port:
        return None
    addresses = info.parsed_scoped_addresses()
    host = next((a for a in addresses if ":" not in a), addresses[0] if addresses else None)
    if host is None:
        return None
    return host, info.port
