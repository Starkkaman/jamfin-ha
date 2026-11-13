"""The "Jamfin moments" event entity."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import JamfinConfigEntry
from .const import MOMENT_EVENTS
from .entity import JamfinEntity

PARALLEL_UPDATES = 0

MOMENTS = EventEntityDescription(
    key="moments",
    translation_key="moments",
    event_types=list(MOMENT_EVENTS),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JamfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the event entity."""
    async_add_entities([JamfinMomentsEvent(entry, MOMENTS)])


class JamfinMomentsEvent(JamfinEntity, EventEntity):
    """Pre-show, movie night, credits, next up, item finished.

    ``segment_started`` and ``chapter_changed`` are device triggers only (too frequent
    for the entity's history, PROTOCOL.md section 8).
    """

    async def async_added_to_hass(self) -> None:
        """Follow the client's events."""
        await super().async_added_to_hass()
        self.async_on_remove(self.jamfin.async_add_event_listener(self._handle_event))

    @callback
    def _handle_event(self, event: str, data: dict[str, Any]) -> None:
        if event not in MOMENT_EVENTS:
            return
        self._trigger_event(event, data)
        self.async_write_ha_state()
