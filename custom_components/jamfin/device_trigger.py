"""Device triggers for Jamfin: moments, segment/chapter events and playback transitions."""

from __future__ import annotations

from typing import Any

import probatio

from homeassistant.components.device_automation import (
    DEVICE_TRIGGER_BASE_SCHEMA,
    InvalidDeviceAutomationConfig,
)
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import (
    DERIVED_TRIGGERS,
    DOMAIN,
    EVENT_JAMFIN,
    MOMENT_EVENTS,
    SEGMENT_KINDS,
    TRIGGER_ONLY_EVENTS,
)

CONF_KIND = "kind"
PROTOCOL_TRIGGERS = MOMENT_EVENTS + TRIGGER_ONLY_EVENTS
TRIGGER_TYPES = PROTOCOL_TRIGGERS + DERIVED_TRIGGERS

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        probatio.Required(CONF_TYPE): probatio.In(TRIGGER_TYPES),
        probatio.Optional(CONF_KIND): probatio.In(SEGMENT_KINDS),
    }
)


def _offered_events(hass: HomeAssistant, device_id: str) -> frozenset[str] | None:
    """Protocol events the TV announced, ``None`` when unknown."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise InvalidDeviceAutomationConfig(f"Device {device_id} not found")
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            continue
        if entry.state is ConfigEntryState.LOADED and (
            capabilities := entry.runtime_data.capabilities
        ):
            return capabilities.events
        return None
    raise InvalidDeviceAutomationConfig(f"Device {device_id} is not a Jamfin TV")


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict[str, Any]]:
    """List the triggers of a Jamfin TV."""
    offered = _offered_events(hass, device_id)
    types = [
        trigger for trigger in PROTOCOL_TRIGGERS if offered is None or trigger in offered
    ] + list(DERIVED_TRIGGERS)
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: trigger,
        }
        for trigger in types
    ]


async def async_get_trigger_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> dict[str, probatio.Schema]:
    """Extra fields: the segment kind for segment_started."""
    if config[CONF_TYPE] == "segment_started":
        return {
            "extra_fields": probatio.Schema(
                {probatio.Optional(CONF_KIND): probatio.In(SEGMENT_KINDS)}
            )
        }
    return {}


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Listen for ``jamfin_event`` of this device and type."""
    event_data: dict[str, Any] = {
        CONF_DEVICE_ID: config[CONF_DEVICE_ID],
        "event": config[CONF_TYPE],
    }
    if config[CONF_TYPE] == "segment_started" and CONF_KIND in config:
        event_data[CONF_KIND] = config[CONF_KIND]
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: EVENT_JAMFIN,
            event_trigger.CONF_EVENT_DATA: event_data,
        }
    )
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
