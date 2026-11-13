"""Audio and subtitle track selects of a Jamfin TV."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import JamfinConfigEntry
from .const import DOMAIN
from .entity import JamfinEntity, async_add_gated_entities, async_send_command
from .models import Track, Tracks

PARALLEL_UPDATES = 1

OPTION_OFF = "Off"


@dataclass(frozen=True, kw_only=True)
class JamfinSelectEntityDescription(SelectEntityDescription):
    """Describes a track select."""

    required_command: str
    subtitles: bool


SELECTS = (
    JamfinSelectEntityDescription(
        key="audio_track",
        translation_key="audio_track",
        required_command="set_audio_track",
        subtitles=False,
    ),
    JamfinSelectEntityDescription(
        key="subtitle_track",
        translation_key="subtitle_track",
        required_command="set_subtitle_track",
        subtitles=True,
    ),
)


def track_options(tracks: tuple[Track, ...]) -> dict[str, int]:
    """Label -> Jellyfin stream index; duplicate labels get the index appended."""
    bases = [t.title or t.language or f"Track {t.index}" for t in tracks]
    counts = Counter(bases)
    return {
        (base if counts[base] == 1 else f"{base} (#{track.index})"): track.index
        for base, track in zip(bases, tracks, strict=True)
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JamfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the track selects."""
    async_add_gated_entities(
        entry, SELECTS, lambda d: JamfinTrackSelect(entry, d), async_add_entities
    )


class JamfinTrackSelect(JamfinEntity, SelectEntity):
    """Audio track or subtitles of the open player."""

    entity_description: JamfinSelectEntityDescription

    def _tracks(self) -> Tracks | None:
        state = self.jamfin_state
        return state.tracks if state is not None else None

    def _options(self) -> dict[str, int | None]:
        if (tracks := self._tracks()) is None:
            return {}
        if self.entity_description.subtitles:
            return {OPTION_OFF: None, **track_options(tracks.subtitle)}
        return dict(track_options(tracks.audio))

    @property
    def available(self) -> bool:
        """Only while a Jellyfin player with tracks is open."""
        return super().available and self._tracks() is not None

    @property
    def options(self) -> list[str]:
        """Track labels."""
        return list(self._options())

    @property
    def current_option(self) -> str | None:
        """The selected track."""
        if (tracks := self._tracks()) is None:
            return None
        selected = (
            tracks.subtitle_selected if self.entity_description.subtitles else tracks.audio_selected
        )
        for label, index in self._options().items():
            if index == selected:
                return label
        return None

    async def async_select_option(self, option: str) -> None:
        """Switch track."""
        options = self._options()
        if option not in options:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_track",
                translation_placeholders={"option": option},
            )
        index = options[option]
        args = {"off": True} if index is None else {"index": index}
        await async_send_command(self.jamfin, self.entity_description.required_command, args)
