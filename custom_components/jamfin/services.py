"""Actions of the Jamfin integration (work without the optional media player)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import probatio

from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv, service

from .const import DOMAIN
from .entity import async_send_command

if TYPE_CHECKING:
    from . import JamfinConfigEntry

SERVICE_PLAY_ITEM = "play_item"
SERVICE_SEEK = "seek"

ATTR_JELLYFIN_ITEM_ID = "jellyfin_item_id"
ATTR_START_POSITION = "start_position"
ATTR_POSITION = "position"

# 32-hex "N" form or a dashed UUID (PROTOCOL.md section 3)
JELLYFIN_ID = probatio.All(
    cv.string,
    probatio.Match(
        r"^([0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
    ),
)
POSITION = probatio.All(probatio.Coerce(float), probatio.Range(min=0))

PLAY_ITEM_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        probatio.Required(ATTR_JELLYFIN_ITEM_ID): JELLYFIN_ID,
        probatio.Optional(ATTR_START_POSITION): POSITION,
    }
)
SEEK_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        probatio.Required(ATTR_POSITION): POSITION,
    }
)


def _entry(call: ServiceCall) -> JamfinConfigEntry:
    return service.async_get_config_entry(call.hass, DOMAIN, call.data[ATTR_CONFIG_ENTRY_ID])


async def _async_play_item(call: ServiceCall) -> None:
    args: dict[str, Any] = {ATTR_JELLYFIN_ITEM_ID: call.data[ATTR_JELLYFIN_ITEM_ID]}
    if ATTR_START_POSITION in call.data:
        args[ATTR_START_POSITION] = call.data[ATTR_START_POSITION]
    await async_send_command(_entry(call).runtime_data, "play_media", args)


async def _async_seek(call: ServiceCall) -> None:
    await async_send_command(
        _entry(call).runtime_data, "seek", {ATTR_POSITION: call.data[ATTR_POSITION]}
    )


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions."""
    hass.services.async_register(
        DOMAIN, SERVICE_PLAY_ITEM, _async_play_item, schema=PLAY_ITEM_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_SEEK, _async_seek, schema=SEEK_SCHEMA)
