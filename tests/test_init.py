"""Setup/unload, connection lifecycle, reconnect, availability, auth failures."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jamfin.const import (
    CONF_APP_VERSION,
    CONF_CAPABILITIES,
    CONF_ZEROCONF_NAME,
    DOMAIN,
    EVENT_JAMFIN,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .conftest import state_of, wait_for
from .fake_jamfin import DEVICE_ID, FakeJamfin

PLAYBACK = "sensor.living_room_tv_playback"
PLAYER = "media_player.living_room_tv"


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_jamfin: FakeJamfin
) -> None:
    """The entry connects, creates the device, and closes the socket on unload."""
    assert init_integration.state is ConfigEntryState.LOADED
    assert fake_jamfin.ws_connects == 1
    assert state_of(hass, PLAYBACK) == "playing"

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, DEVICE_ID), init_integration.entry_id
    )
    assert device is not None
    assert device.name == "Living Room TV"
    assert device.model == "SmartTV 4K FFM"
    assert device.sw_version == "0.5.0-beta5"
    assert device.manufacturer == "Jamfin"
    # Capabilities are cached so gated entities exist while the TV is off at startup.
    assert "colors" in init_integration.data[CONF_CAPABILITIES]["features"]

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.NOT_LOADED
    await wait_for(hass, lambda: not fake_jamfin.sockets)


async def test_hello_updates_device(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_jamfin: FakeJamfin
) -> None:
    """A new app version from hello reaches the device registry and the entry."""
    fake_jamfin.hello["device"]["app_version"] = "0.5.1-beta5"
    await fake_jamfin.close_sockets(1001)
    await wait_for(hass, lambda: fake_jamfin.ws_connects == 2 and bool(fake_jamfin.sockets))
    await wait_for(hass, lambda: init_integration.data[CONF_APP_VERSION] == "0.5.1-beta5")
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, DEVICE_ID), init_integration.entry_id
    )
    assert device.sw_version == "0.5.1-beta5"


async def test_unreachable_at_startup(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """An unreachable TV is not a setup error: it's off and entities are unavailable."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**mock_config_entry.data, CONF_PORT: 1}
    )
    with patch("custom_components.jamfin.client.BACKOFF_STEPS", (3600.0,)):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        assert mock_config_entry.state is ConfigEntryState.LOADED
        assert state_of(hass, PLAYER) == "off"
        assert state_of(hass, PLAYBACK) == "unavailable"
        # Nothing cached yet: capability-gated entities wait for the first hello.
        assert hass.states.get("sensor.living_room_tv_film_colour") is None
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_reconnect_and_availability(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_jamfin: FakeJamfin
) -> None:
    """Socket lost: off/unavailable; back: a fresh snapshot replaces the old state."""
    events = []
    hass.bus.async_listen(EVENT_JAMFIN, events.append)

    fake_jamfin.reject_ws_status = 503
    await fake_jamfin.close_sockets(1001)
    await wait_for(hass, lambda: state_of(hass, PLAYER) == "off")
    assert state_of(hass, PLAYBACK) == "unavailable"
    assert state_of(hass, "button.living_room_tv_skip_segment") == "unavailable"
    assert [e.data["event"] for e in events] == ["turned_off"]
    assert events[0].data["derived"] is True

    # While it was away the TV stopped playing; the snapshot replaces, never merges.
    fake_jamfin.state = fake_jamfin.fixture_state("state_idle.json")
    fake_jamfin.reject_ws_status = None
    await wait_for(hass, lambda: state_of(hass, PLAYBACK) == "idle")
    assert state_of(hass, PLAYER) == "idle"
    assert state_of(hass, "sensor.living_room_tv_chapter") == "unknown"
    # No "stopped" transition from comparing against pre-disconnect state.
    assert [e.data["event"] for e in events] == ["turned_off"]
    client = init_integration.runtime_data
    assert client.stats["connects"] == 2
    assert client.stats["disconnects"] == 1


async def test_auth_revoked_starts_reauth(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_jamfin: FakeJamfin
) -> None:
    """Close 4401: stop reconnecting and start a reauth flow."""
    fake_jamfin.clients.clear()
    await fake_jamfin.close_sockets(4401)
    await wait_for(
        hass,
        lambda: any(
            flow["context"]["source"] == SOURCE_REAUTH
            for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        ),
    )
    client = init_integration.runtime_data
    assert client.auth_failed
    assert state_of(hass, PLAYER) == "off"
    connects = fake_jamfin.ws_connects
    await asyncio.sleep(0.2)
    assert fake_jamfin.ws_connects == connects


async def test_setup_with_refused_key(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_jamfin: FakeJamfin
) -> None:
    """A 401 on the upgrade during setup is ConfigEntryAuthFailed (reauth)."""
    fake_jamfin.clients.clear()
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_replaced_socket_stops(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_jamfin: FakeJamfin
) -> None:
    """Close 4000 (a newer socket uses the key): this client stops."""
    await fake_jamfin.close_sockets(4000)
    await wait_for(hass, lambda: init_integration.runtime_data.replaced)
    await asyncio.sleep(0.2)
    assert fake_jamfin.ws_connects == 1
    assert state_of(hass, PLAYER) == "off"


async def test_heartbeat_and_pong_timeout(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_jamfin: FakeJamfin
) -> None:
    """JSON pings at hello's interval; no pong in time -> reconnect."""
    fake_jamfin.hello["heartbeat"] = {"ping_interval": 0.05, "pong_timeout": 0.1}
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await wait_for(hass, lambda: fake_jamfin.pings >= 3)
    assert fake_jamfin.ws_connects == 1
    assert state_of(hass, PLAYBACK) == "playing"

    fake_jamfin.answer_pings = False
    await wait_for(hass, lambda: fake_jamfin.ws_connects >= 2)
    assert "pong" in init_integration_error(mock_config_entry)
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


def init_integration_error(entry: MockConfigEntry) -> str:
    """Last connection error recorded by the client."""
    return entry.runtime_data.stats["last_error"] or ""


async def test_seq_and_unknown_messages_tolerated(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_jamfin: FakeJamfin
) -> None:
    """Unknown message types, unknown fields and unknown enums don't break anything."""
    ws = next(iter(fake_jamfin.sockets))
    await ws.send_json({"type": "teleport", "id": 5})
    await ws.send_str("not json")
    state = fake_jamfin.fixture_state("state_episode.json")
    state["player"]["state"] = "warp_speed"
    state["media"]["kind"] = "hologram"
    state["brand_new_field"] = {"x": 1}
    await fake_jamfin.push_state(state)
    await wait_for(hass, lambda: state_of(hass, PLAYBACK) == "unknown")
    assert state_of(hass, "sensor.living_room_tv_media_kind") == "unknown"
    assert state_of(hass, PLAYER) == "on"
    assert init_integration.runtime_data.last_seq == 2


async def test_capability_gating(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_jamfin: FakeJamfin
) -> None:
    """In disc menu appears when a hello lists disc_relay (beta 5b)."""
    entity_id = "binary_sensor.living_room_tv_in_disc_menu"
    assert hass.states.get(entity_id) is None
    fake_jamfin.hello["capabilities"]["features"].append("disc_relay")
    disc = fake_jamfin.fixture_state("state_disc_beta5.json")
    disc["player"]["state"] = "playing"
    disc["player"]["in_disc_menu"] = True
    fake_jamfin.state = disc
    await fake_jamfin.close_sockets(1001)
    await wait_for(hass, lambda: state_of(hass, entity_id) == "on")
    assert "disc_relay" in init_integration.data[CONF_CAPABILITIES]["features"]


async def test_no_images_or_colours_without_features(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_jamfin: FakeJamfin
) -> None:
    """Image and colour entities need the images/colors features."""
    fake_jamfin.hello["capabilities"]["features"] = []
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert state_of(hass, PLAYBACK) == "playing"
    assert hass.states.get("sensor.living_room_tv_film_colour") is None
    assert hass.states.get("image.living_room_tv_poster") is None
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_zeroconf_re_resolve_after_failures(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_jamfin: FakeJamfin
) -> None:
    """After 3 failed attempts the address comes from zeroconf and is saved."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            CONF_PORT: 1,
            CONF_ZEROCONF_NAME: "Living Room TV._jamfin._tcp.local.",
        },
    )
    resolver = AsyncMock(return_value=("127.0.0.1", fake_jamfin.port))
    with patch("custom_components.jamfin._async_resolve", resolver):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await wait_for(hass, lambda: state_of(hass, PLAYBACK) == "playing")
    assert resolver.await_count >= 1
    assert mock_config_entry.data[CONF_PORT] == fake_jamfin.port
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_remove_entry_unpairs(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_jamfin: FakeJamfin
) -> None:
    """Deleting the entry calls DELETE /api/v1/pair."""
    client_id = next(iter(fake_jamfin.clients.values()))["client_id"]
    assert await hass.config_entries.async_remove(init_integration.entry_id)
    await hass.async_block_till_done()
    assert fake_jamfin.unpaired == [client_id]
    assert not fake_jamfin.clients
