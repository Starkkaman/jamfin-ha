# jamfin-ha

A Home Assistant custom integration (`jamfin`) for **Jamfin TV**, our own Jellyfin client for Android TV
(repo `Starkkaman/Jamfin`). Jamfin runs a small local server on the TV; this integration finds it on the LAN, pairs
with a PIN shown on the TV, and keeps one WebSocket open per TV. Home Assistant then gets live playback state,
Jamfin's "moments" (pre-show, movie night, credits, next up, intros/recaps), the active profile, chapters, tracks,
film artwork and colours, and can send commands (play, pause, seek, skip intro, change tracks, play a title).

**Status: spec draft (2026-11-12).** No code yet. Nothing here talks to a TV or a Home Assistant yet.

- [docs/PROTOCOL.md](docs/PROTOCOL.md): the wire contract between Jamfin (server, on the TV) and this integration
  (client): discovery, pairing, REST, WebSocket, state model, events, commands, versioning, security.
- [docs/HA-INTEGRATION.md](docs/HA-INTEGRATION.md): the plan for the Home Assistant side (config flow, entities,
  coordinator, tests, HACS).

Planned GitHub repo: `Starkkaman/jamfin-ha` (not created; this repo is local only for now).
