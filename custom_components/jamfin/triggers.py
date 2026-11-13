"""Playback transitions derived from state pushes (PROTOCOL.md section 8, decisions)."""

from __future__ import annotations

from .models import JamfinState

_PLAYING = frozenset({"playing", "buffering"})
_ACTIVE = frozenset({"playing", "paused", "buffering"})
_RESTING = frozenset({"idle", "stopped"})


def _item_id(state: JamfinState) -> str | None:
    return state.media.jellyfin_item_id if state.media is not None else None


def derive_transitions(previous: JamfinState | None, new: JamfinState) -> list[str]:
    """Return the derived triggers for one state change.

    ``previous`` is ``None`` for the snapshot after a (re)connect: nothing fires then,
    because the client never merges with pre-disconnect state.
    """
    if previous is None:
        return []
    before, after = previous.player_state, new.player_state
    transitions: list[str] = []
    if after in _PLAYING and (
        before in _RESTING
        or (
            before in _ACTIVE
            and _item_id(previous) is not None
            and _item_id(new) is not None
            and _item_id(previous) != _item_id(new)
        )
    ):
        transitions.append("started_playing")
    elif after in _PLAYING and before == "paused":
        transitions.append("resumed")
    if after == "paused" and before in _PLAYING:
        transitions.append("paused")
    if after == "stopped" and before in _ACTIVE | {"unknown"}:
        transitions.append("stopped")
    return transitions
