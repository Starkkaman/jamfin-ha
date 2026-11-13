"""Sensors of a Jamfin TV."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import JamfinConfigEntry
from .const import FEATURE_COLORS, MEDIA_KINDS, PRIVATE, SEGMENT_KINDS
from .entity import JamfinEntity, async_add_gated_entities
from .models import FilmColors, JamfinState

PARALLEL_UPDATES = 0

PLAYBACK_OPTIONS = ["idle", "playing", "paused", "buffering", "stopped"]
MEDIA_KIND_OPTIONS = [*MEDIA_KINDS, "none", PRIVATE]
SEGMENT_OPTIONS = [*SEGMENT_KINDS, "none"]


@dataclass(frozen=True, kw_only=True)
class JamfinSensorEntityDescription(SensorEntityDescription):
    """Describes a Jamfin sensor."""

    value_fn: Callable[[JamfinState], StateType]
    attrs_fn: Callable[[JamfinState], dict[str, Any]] | None = None
    required_feature: str | None = None


def _playback(state: JamfinState) -> str | None:
    return state.player_state if state.player_state in PLAYBACK_OPTIONS else None


def _playback_attrs(state: JamfinState) -> dict[str, Any]:
    media = state.media
    return {
        "private": state.private,
        "source": state.source,
        "app_state": state.app_state,
        "title": media.title if media else None,
        "kind": media.kind if media else None,
        "series": media.series if media else None,
    }


def _profile(state: JamfinState) -> str | None:
    return PRIVATE if state.private else state.profile_name


def _media_kind(state: JamfinState) -> str | None:
    if state.private:
        return PRIVATE
    if state.media is None:
        return "none"
    return state.media.kind


def _chapter(state: JamfinState) -> str | None:
    if state.chapters is None or (chapter := state.chapters.current_chapter) is None:
        return None
    return chapter.name or f"Chapter {state.chapters.current + 1}"  # type: ignore[operator]


def _chapter_attrs(state: JamfinState) -> dict[str, Any]:
    chapters = state.chapters
    chapter = chapters.current_chapter if chapters else None
    return {
        "index": chapters.current if chapters else None,
        "start": chapter.start if chapter else None,
        "count": len(chapters.items) if chapters else 0,
    }


def _segment(state: JamfinState) -> str | None:
    if state.private:
        return None
    if state.segment is None:
        return "none"
    return state.segment.kind


def _segment_attrs(state: JamfinState) -> dict[str, Any]:
    segment = state.segment
    return {
        "start": segment.start if segment else None,
        "end": segment.end if segment else None,
        "action": segment.action if segment else None,
    }


def _colors(state: JamfinState) -> FilmColors | None:
    return state.media.colors if state.media is not None else None


def _colour(state: JamfinState) -> str | None:
    colors = _colors(state)
    return colors.dominant if colors else None


def _colour_attrs(state: JamfinState) -> dict[str, Any]:
    colors = _colors(state)
    if colors is None:
        return {
            "accent": None,
            "palette": [],
            "dominant_rgb": None,
            "accent_rgb": None,
            "source": None,
        }
    return {
        "accent": colors.accent,
        "palette": list(colors.palette),
        "dominant_rgb": colors.dominant_rgb,
        "accent_rgb": colors.accent_rgb,
        "source": colors.source,
    }


SENSORS: tuple[JamfinSensorEntityDescription, ...] = (
    JamfinSensorEntityDescription(
        key="playback",
        translation_key="playback",
        device_class=SensorDeviceClass.ENUM,
        options=PLAYBACK_OPTIONS,
        value_fn=_playback,
        attrs_fn=_playback_attrs,
    ),
    JamfinSensorEntityDescription(
        key="profile",
        translation_key="profile",
        value_fn=_profile,
        attrs_fn=lambda state: {"profile_id": state.profile_id, "private": state.private},
    ),
    JamfinSensorEntityDescription(
        key="media_kind",
        translation_key="media_kind",
        device_class=SensorDeviceClass.ENUM,
        options=MEDIA_KIND_OPTIONS,
        value_fn=_media_kind,
    ),
    JamfinSensorEntityDescription(
        key="chapter",
        translation_key="chapter",
        value_fn=_chapter,
        attrs_fn=_chapter_attrs,
    ),
    JamfinSensorEntityDescription(
        key="segment",
        translation_key="segment",
        device_class=SensorDeviceClass.ENUM,
        options=SEGMENT_OPTIONS,
        value_fn=_segment,
        attrs_fn=_segment_attrs,
    ),
    JamfinSensorEntityDescription(
        key="film_colour",
        translation_key="film_colour",
        value_fn=_colour,
        attrs_fn=_colour_attrs,
        required_feature=FEATURE_COLORS,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JamfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    async_add_gated_entities(entry, SENSORS, lambda d: JamfinSensor(entry, d), async_add_entities)


class JamfinSensor(JamfinEntity, SensorEntity):
    """A Jamfin sensor."""

    entity_description: JamfinSensorEntityDescription

    @property
    def native_value(self) -> StateType:
        """The sensor value."""
        if (state := self.jamfin_state) is None:
            return None
        return self.entity_description.value_fn(state)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Extra attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        if (state := self.jamfin_state) is None:
            return None
        return self.entity_description.attrs_fn(state)
