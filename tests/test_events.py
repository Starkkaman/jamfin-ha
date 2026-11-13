"""Event entity, the jamfin_event bus event and device triggers."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_get_device_automations,
)

from custom_components.jamfin.const import DOMAIN, EVENT_JAMFIN
from homeassistant.components import automation
from homeassistant.components.device_automation import DeviceAutomationType
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component

from .conftest import state_of, wait_for
from .fake_jamfin import DEVICE_ID, FakeJamfin

MOMENTS = "event.living_room_tv_jamfin_moments"
ITEM = "4f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f"

pytestmark = pytest.mark.usefixtures("init_integration")


def _device_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, DEVICE_ID), entry.entry_id)
    assert device is not None
    return device.id


async def test_event_entity(hass: HomeAssistant, fake_jamfin: FakeJamfin) -> None:
    """Moments reach the event entity; chapter/segment events don't."""
    assert state_of(hass, MOMENTS) == "unknown"
    await fake_jamfin.push_event(
        "credits_started",
        {
            "jellyfin_item_id": ITEM,
            "kind": "episode",
            "position": 3101.0,
            "source": "segment",
            "has_next": True,
        },
    )
    await wait_for(hass, lambda: state_of(hass, MOMENTS) != "unknown")
    state = hass.states.get(MOMENTS)
    assert state.attributes["event_type"] == "credits_started"
    assert state.attributes["has_next"] is True
    assert state.attributes["event_types"] == [
        "preshow",
        "movienight",
        "movienight_winner",
        "credits_started",
        "next_up",
        "item_finished",
    ]

    first = state.state
    await fake_jamfin.push_event("chapter_changed", {"jellyfin_item_id": ITEM, "index": 1})
    await fake_jamfin.push_event("next_up", {"jellyfin_item_id": ITEM, "countdown": 10})
    await wait_for(hass, lambda: hass.states.get(MOMENTS).attributes["event_type"] == "next_up")
    assert hass.states.get(MOMENTS).state != first


async def test_bus_events(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_jamfin: FakeJamfin
) -> None:
    """Every known protocol event fires jamfin_event with the HA device id."""
    events = async_capture_events(hass, EVENT_JAMFIN)
    await fake_jamfin.push_event("time_travel", {"x": 1})  # unknown: ignored
    await fake_jamfin.push_event(
        "segment_started",
        {"jellyfin_item_id": ITEM, "kind": "intro", "start": 5.0, "end": 95.0, "action": "ask"},
    )
    await wait_for(hass, lambda: len(events) == 1)
    data = events[0].data
    assert data["device_id"] == _device_id(hass, init_integration)
    assert data["jamfin_id"] == DEVICE_ID
    assert data["event"] == "segment_started"
    assert data["kind"] == "intro"
    assert data["derived"] is False
    assert data["data"]["end"] == 95.0


async def test_device_trigger_list(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Moments, segment/chapter and derived transitions are offered."""
    triggers = await async_get_device_automations(
        hass, DeviceAutomationType.TRIGGER, _device_id(hass, init_integration)
    )
    types = {t["type"] for t in triggers if t["domain"] == DOMAIN}
    assert types == {
        "preshow",
        "movienight",
        "movienight_winner",
        "credits_started",
        "next_up",
        "item_finished",
        "segment_started",
        "chapter_changed",
        "started_playing",
        "paused",
        "resumed",
        "stopped",
        "turned_off",
    }


async def _automation(hass: HomeAssistant, device_id: str, trigger_type: str, **extra) -> None:
    assert await async_setup_component(
        hass,
        automation.DOMAIN,
        {
            automation.DOMAIN: [
                {
                    "triggers": {
                        "platform": "device",
                        "domain": DOMAIN,
                        "device_id": device_id,
                        "type": trigger_type,
                        **extra,
                    },
                    "actions": {
                        "action": "test.automation",
                        "data": {"some": f"{trigger_type} {{{{ trigger.event.data.event }}}}"},
                    },
                }
            ]
        },
    )


async def test_device_trigger_moment(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_jamfin: FakeJamfin,
    service_calls: list[ServiceCall],
) -> None:
    """A credits_started device trigger fires once per event."""
    await _automation(hass, _device_id(hass, init_integration), "credits_started")
    await fake_jamfin.push_event("item_finished", {"jellyfin_item_id": ITEM})
    await fake_jamfin.push_event("credits_started", {"jellyfin_item_id": ITEM})
    await wait_for(hass, lambda: len(service_calls) == 1)
    assert service_calls[0].data["some"] == "credits_started credits_started"


async def test_device_trigger_segment_kind(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_jamfin: FakeJamfin,
    service_calls: list[ServiceCall],
) -> None:
    """segment_started can be narrowed to one kind."""
    await _automation(hass, _device_id(hass, init_integration), "segment_started", kind="intro")
    await fake_jamfin.push_event("segment_started", {"jellyfin_item_id": ITEM, "kind": "recap"})
    await fake_jamfin.push_event("segment_started", {"jellyfin_item_id": ITEM, "kind": "intro"})
    await wait_for(hass, lambda: len(service_calls) == 1)
    await fake_jamfin.push_event("chapter_changed", {"jellyfin_item_id": ITEM})
    await hass.async_block_till_done()
    assert len(service_calls) == 1


async def test_derived_transitions(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_jamfin: FakeJamfin,
) -> None:
    """paused, resumed, stopped, started_playing come from state changes."""
    events = async_capture_events(hass, EVENT_JAMFIN)
    playing = fake_jamfin.fixture_state("state_episode.json")

    def with_state(value: str) -> dict:
        state = fake_jamfin.fixture_state("state_episode.json")
        state["player"]["state"] = value
        return state

    stopped = fake_jamfin.fixture_state("state_idle.json")
    stopped["player"]["state"] = "stopped"
    for state in (
        with_state("paused"),
        with_state("buffering"),
        playing,
        stopped,
        with_state("buffering"),
    ):
        await fake_jamfin.push_state(state)
    await wait_for(hass, lambda: len(events) == 4)
    assert [(e.data["event"], e.data["derived"]) for e in events] == [
        ("paused", True),
        ("resumed", True),
        ("stopped", True),
        ("started_playing", True),
    ]

    # Auto-advance to the next episode (no "stopped" in between) is a new start.
    nxt = with_state("playing")
    nxt["media"]["jellyfin_item_id"] = "8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d"
    await fake_jamfin.push_state(nxt)
    await wait_for(hass, lambda: len(events) == 5)
    assert events[-1].data["event"] == "started_playing"


async def test_device_trigger_turned_off(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_jamfin: FakeJamfin,
    service_calls: list[ServiceCall],
) -> None:
    """turned_off fires when the TV becomes unreachable."""
    await _automation(hass, _device_id(hass, init_integration), "turned_off")
    fake_jamfin.reject_ws_status = 503
    await fake_jamfin.close_sockets(1001)
    await wait_for(hass, lambda: len(service_calls) == 1)


async def test_no_events_while_unloading(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Unloading doesn't fire a fake turned_off."""
    events = async_capture_events(hass, EVENT_JAMFIN)
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert events == []
