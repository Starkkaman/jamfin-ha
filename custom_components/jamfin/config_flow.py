"""Config flow for Jamfin: zeroconf or manual host, PIN pairing, reauth, options."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

import probatio

from homeassistant.config_entries import (
    SOURCE_IGNORE,
    SOURCE_REAUTH,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import instance_id
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import JamfinApi, JamfinApiError, JamfinConnectionError, JamfinError
from .const import (
    CONF_APP_VERSION,
    CONF_CLIENT_ID,
    CONF_CLIENT_KEY,
    CONF_JAMFIN_ID,
    CONF_MEDIA_PLAYER,
    CONF_MODEL,
    CONF_PIN,
    CONF_ZEROCONF_NAME,
    DEFAULT_PORT,
    DOMAIN,
    LOGGER,
    SUPPORTED_API_VERSIONS,
    ZEROCONF_TYPE,
)

USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_HOST): str,
        probatio.Required(CONF_PORT, default=DEFAULT_PORT): probatio.All(
            probatio.Coerce(int), probatio.Range(min=1, max=65535)
        ),
    }
)
PIN_SCHEMA = probatio.Schema({probatio.Required(CONF_PIN): str})

# pair/start error codes shown on the step that asked for the PIN
_START_ERRORS = {
    "not_in_foreground": "not_in_foreground",
    "not_foreground": "not_in_foreground",
    "pairing_in_progress": "pairing_in_progress",
    "too_many_clients": "too_many_clients",
    "rate_limited": "rate_limited",
}


def core_jellyfin_configured(hass: HomeAssistant) -> bool:
    """Whether the core Jellyfin integration is set up (PROTOCOL.md section 13)."""
    return any(
        entry.source != SOURCE_IGNORE for entry in hass.config_entries.async_entries("jellyfin")
    )


def _api_supported(info: Mapping[str, Any]) -> bool:
    versions = info.get("api_versions")
    if isinstance(versions, list) and versions:
        return any(v in SUPPORTED_API_VERSIONS for v in versions)
    return info.get("api") in SUPPORTED_API_VERSIONS


class JamfinConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pair Home Assistant with a Jamfin TV."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialise."""
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._name: str | None = None
        self._zeroconf_name: str | None = None
        self._pairing_id: str | None = None
        self._placeholders: dict[str, str] = {}

    def _api(self) -> JamfinApi:
        assert self._host is not None
        return JamfinApi(async_get_clientsession(self.hass), self._host, self._port)

    def _description_placeholders(self) -> dict[str, str]:
        return {
            "name": self._name or self._host or "",
            "retry_after": "a few",
            "attempts_left": "?",
            **self._placeholders,
        }

    # ------------------------------------------------------------------ discovery

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Handle a TV that announced ``_jamfin._tcp``."""
        props = discovery_info.properties
        jamfin_id = props.get("id")
        if not isinstance(jamfin_id, str) or not jamfin_id:
            return self.async_abort(reason="invalid_discovery_info")
        try:
            api = int(props.get("api", ""))
        except ValueError:
            return self.async_abort(reason="invalid_discovery_info")
        if api < min(SUPPORTED_API_VERSIONS):
            return self.async_abort(reason="unsupported_api")
        host = next(
            (str(ip) for ip in discovery_info.ip_addresses if ip.version == 4),
            discovery_info.host,
        )
        port = discovery_info.port or DEFAULT_PORT

        await self.async_set_unique_id(jamfin_id)
        entry = self.hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, jamfin_id)
        if entry is not None and entry.state is ConfigEntryState.LOADED:
            # The TV is back (maybe at a new address): reconnect now, no reload.
            entry.runtime_data.async_update_address(host, port)
        self._abort_if_unique_id_configured(
            updates={
                CONF_HOST: host,
                CONF_PORT: port,
                CONF_ZEROCONF_NAME: discovery_info.name,
            },
            reload_on_update=False,
        )

        self._host = host
        self._port = port
        self._zeroconf_name = discovery_info.name
        self._name = props.get("name") or discovery_info.name.removesuffix(f".{ZEROCONF_TYPE}")
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask before the TV shows a PIN (someone has to be at Home Assistant)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await self._api().async_get_info()
            except JamfinError:
                return self.async_abort(reason="cannot_connect")
            if not _api_supported(info):
                return self.async_abort(reason="unsupported_api")
            if (error := await self._async_start_pairing()) is None:
                return await self.async_step_pin()
            errors["base"] = error
        return self.async_show_form(
            step_id="zeroconf_confirm",
            errors=errors,
            description_placeholders=self._description_placeholders(),
        )

    # ------------------------------------------------------------------ manual host

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manual host (mDNS does not cross subnets)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = user_input[CONF_HOST].strip()
            self._port = user_input[CONF_PORT]
            try:
                info = await self._api().async_get_info()
            except JamfinError:
                errors["base"] = "cannot_connect"
            else:
                if not _api_supported(info):
                    errors["base"] = "unsupported_api"
                else:
                    await self.async_set_unique_id(info["id"])
                    self._abort_if_unique_id_configured(
                        updates={CONF_HOST: self._host, CONF_PORT: self._port}
                    )
                    self._name = info.get("name") or self._host
                    if (error := await self._async_start_pairing()) is None:
                        return await self.async_step_pin()
                    errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
            description_placeholders=self._description_placeholders(),
        )

    # ------------------------------------------------------------------ pairing

    async def _async_start_pairing(self) -> str | None:
        """Ask the TV to show a PIN. Return an error key, or ``None`` on success."""
        self._placeholders = {}
        client_name = f"Home Assistant ({self.hass.config.location_name})"[:64]
        try:
            started = await self._api().async_pair_start(
                client_name, await instance_id.async_get(self.hass)
            )
        except JamfinApiError as err:
            if err.retry_after is not None:
                self._placeholders["retry_after"] = str(round(err.retry_after))
            LOGGER.debug("pair/start refused: %s", err)
            return _START_ERRORS.get(err.code, "unknown")
        except JamfinConnectionError:
            return "cannot_connect"
        self._pairing_id = started["pairing_id"]
        return None

    async def async_step_pin(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Enter the PIN shown on the TV."""
        errors: dict[str, str] = {}
        if user_input is not None:
            pin = re.sub(r"\s", "", user_input[CONF_PIN])
            if not re.fullmatch(r"\d{6}", pin):
                errors[CONF_PIN] = "invalid_pin_format"
            else:
                assert self._pairing_id is not None
                try:
                    paired = await self._api().async_pair_confirm(self._pairing_id, pin)
                except JamfinApiError as err:
                    if err.code == "invalid_pin" and err.attempts_left != 0:
                        errors[CONF_PIN] = "invalid_pin"
                        self._placeholders["attempts_left"] = str(
                            err.attempts_left if err.attempts_left is not None else "?"
                        )
                    elif err.code in ("invalid_pin", "pairing_expired", "not_found"):
                        self._pairing_id = None
                        return await self.async_step_pairing_expired()
                    elif err.code == "rate_limited":
                        errors["base"] = "rate_limited"
                        self._placeholders["retry_after"] = str(round(err.retry_after or 0))
                    else:
                        errors["base"] = "unknown"
                except JamfinConnectionError:
                    errors["base"] = "cannot_connect"
                else:
                    self._pairing_id = None
                    return self._async_finish(paired)
        return self.async_show_form(
            step_id="pin",
            data_schema=PIN_SCHEMA,
            errors=errors,
            description_placeholders=self._description_placeholders(),
        )

    async def async_step_pairing_expired(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer a new PIN after the old one timed out or was cancelled."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if (error := await self._async_start_pairing()) is None:
                return await self.async_step_pin()
            errors["base"] = error
        return self.async_show_form(
            step_id="pairing_expired",
            errors=errors,
            description_placeholders=self._description_placeholders(),
        )

    @callback
    def _async_finish(self, paired: Mapping[str, Any]) -> ConfigFlowResult:
        device = paired.get("device") if isinstance(paired.get("device"), Mapping) else {}
        assert isinstance(device, Mapping)
        data: dict[str, Any] = {
            CONF_HOST: self._host,
            CONF_PORT: self._port,
            CONF_CLIENT_ID: paired.get("client_id"),
            CONF_CLIENT_KEY: paired["client_key"],
            CONF_MODEL: device.get("model"),
            CONF_APP_VERSION: device.get("app_version"),
        }
        if self._zeroconf_name:
            data[CONF_ZEROCONF_NAME] = self._zeroconf_name
        if self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            if device.get("id") and device["id"] != entry.unique_id:
                return self.async_abort(reason="unique_id_mismatch")
            return self.async_update_reload_and_abort(entry, data_updates=data)
        if device.get("id") and device["id"] != self.unique_id:
            return self.async_abort(reason="unique_id_mismatch")
        data[CONF_JAMFIN_ID] = self.unique_id
        return self.async_create_entry(
            title=self._name or self._host or "Jamfin",
            data=data,
            options={CONF_MEDIA_PLAYER: not core_jellyfin_configured(self.hass)},
        )

    @callback
    def async_remove(self) -> None:
        """Hide the PIN on the TV when the flow is closed before pairing finished."""
        if self._pairing_id is None or self._host is None:
            return
        pairing_id, self._pairing_id = self._pairing_id, None
        api = self._api()

        async def _cancel() -> None:
            try:
                await api.async_pair_cancel(pairing_id)
            except JamfinError as err:
                LOGGER.debug("pair/cancel failed: %s", err)

        self.hass.async_create_task(_cancel(), "jamfin pair cancel")

    # ------------------------------------------------------------------ reauth

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Pair again after the TV refused the key."""
        self._host = entry_data[CONF_HOST]
        self._port = entry_data[CONF_PORT]
        self._name = self._get_reauth_entry().title
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask before the TV shows a PIN."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if (error := await self._async_start_pairing()) is None:
                return await self.async_step_pin()
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            errors=errors,
            description_placeholders=self._description_placeholders(),
        )

    # ------------------------------------------------------------------ options

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> JamfinOptionsFlow:
        """Options: the media player toggle."""
        return JamfinOptionsFlow()


class JamfinOptionsFlow(OptionsFlowWithReload):
    """Turn the Jamfin media player on or off."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the toggle."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options.get(CONF_MEDIA_PLAYER, True)
        return self.async_show_form(
            step_id="init",
            data_schema=probatio.Schema(
                {probatio.Required(CONF_MEDIA_PLAYER, default=current): bool}
            ),
        )
