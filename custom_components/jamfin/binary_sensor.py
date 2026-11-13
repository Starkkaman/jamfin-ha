"""Binary sensors of a Jamfin TV."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import JamfinConfigEntry
from .const import FEATURE_DISC_RELAY
from .entity import JamfinEntity, async_add_gated_entities

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class JamfinBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a Jamfin binary sensor."""

    required_feature: str | None = None


BINARY_SENSORS = (
    # The disc-menu flag exists from beta 5b (``disc_relay``), PROTOCOL.md section 11.
    JamfinBinarySensorEntityDescription(
        key="in_disc_menu",
        translation_key="in_disc_menu",
        required_feature=FEATURE_DISC_RELAY,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JamfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    async_add_gated_entities(
        entry,
        BINARY_SENSORS,
        lambda d: JamfinBinarySensor(entry, d),
        async_add_entities,
    )


class JamfinBinarySensor(JamfinEntity, BinarySensorEntity):
    """In disc menu."""

    @property
    def is_on(self) -> bool | None:
        """True while the disc's own menu or the house menu shows."""
        if (state := self.jamfin_state) is None:
            return None
        return state.in_disc_menu
