"""Buttons of a Jamfin TV (commands without the media player)."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import JamfinConfigEntry
from .entity import JamfinEntity, async_add_gated_entities, async_send_command

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class JamfinButtonEntityDescription(ButtonEntityDescription):
    """Describes a Jamfin button; ``required_command`` is what it sends."""

    required_command: str


BUTTONS = (
    JamfinButtonEntityDescription(
        key="skip_segment", translation_key="skip_segment", required_command="skip_segment"
    ),
    JamfinButtonEntityDescription(
        key="next_chapter", translation_key="next_chapter", required_command="next_chapter"
    ),
    JamfinButtonEntityDescription(
        key="previous_chapter",
        translation_key="previous_chapter",
        required_command="previous_chapter",
    ),
    JamfinButtonEntityDescription(
        key="go_home", translation_key="go_home", required_command="go_home"
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JamfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons."""
    async_add_gated_entities(entry, BUTTONS, lambda d: JamfinButton(entry, d), async_add_entities)


class JamfinButton(JamfinEntity, ButtonEntity):
    """A command button."""

    entity_description: JamfinButtonEntityDescription

    async def async_press(self) -> None:
        """Send the command."""
        await async_send_command(self.jamfin, self.entity_description.required_command)
