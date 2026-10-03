"""Config flow para Dreame SF25."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD

from .api import DreameApiError, DreameAuthError, DreameSF25Client
from .const import (
    CONF_AUTO_MODES,
    CONF_DID,
    CONF_REGION,
    CONF_SAFETY_STOP,
    CONF_VIRTUAL_MODES,
    DEFAULT_REGION,
    DOMAIN,
    REGIONS,
)

_LOGGER = logging.getLogger(__name__)


class DreameSF25ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Flujo de configuracion (email + contrasena + region)."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry):
        return DreameSF25OptionsFlow(config_entry)

    async def _async_validate(
        self, email: str, password: str, region: str, did: str | None, errors: dict[str, str]
    ) -> dict | None:
        """Comprueba las credenciales; devuelve el dispositivo o rellena errors."""
        client = DreameSF25Client(email, password, region)
        try:
            return await self.hass.async_add_executor_job(client.resolve_device, did)
        except DreameAuthError:
            errors["base"] = "invalid_auth"
        except DreameApiError:
            errors["base"] = "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Error inesperado en config flow")
            errors["base"] = "unknown"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            device = await self._async_validate(
                user_input[CONF_EMAIL],
                user_input[CONF_PASSWORD],
                user_input[CONF_REGION],
                None,
                errors,
            )
            if device is not None:
                did = str(device.get("did"))
                await self.async_set_unique_id(did)
                self._abort_if_unique_id_configured()
                name = device.get("customName") or device.get("deviceInfo", {}).get(
                    "displayName", "Dreame SF25"
                )
                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_EMAIL: user_input[CONF_EMAIL],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_REGION: user_input[CONF_REGION],
                        CONF_DID: did,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_REGION, default=DEFAULT_REGION): vol.In(REGIONS),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """La nube rechazo la contrasena guardada."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pide la contrasena nueva y recarga la entrada."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            device = await self._async_validate(
                entry.data[CONF_EMAIL],
                user_input[CONF_PASSWORD],
                entry.data.get(CONF_REGION, DEFAULT_REGION),
                entry.data.get(CONF_DID),
                errors,
            )
            if device is not None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )


class DreameSF25OptionsFlow(OptionsFlow):
    """Opt-in for actions initiated by the integration itself."""

    def __init__(self, config_entry):
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        options = self._entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_AUTO_MODES, default=options.get(CONF_AUTO_MODES, False)
                    ): bool,
                    vol.Required(
                        CONF_SAFETY_STOP, default=options.get(CONF_SAFETY_STOP, False)
                    ): bool,
                    vol.Required(
                        CONF_VIRTUAL_MODES,
                        default=options.get(CONF_VIRTUAL_MODES, False),
                    ): bool,
                }
            ),
        )
