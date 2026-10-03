"""Integracion Dreame SF25 Waste Disposer (nube Dreamehome, ingenieria inversa)."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .api import DreameApiError, DreameAuthError, DreameSF25Client
from .const import CONF_DID, CONF_REGION, DEFAULT_REGION, DOMAIN
from .coordinator import DreameSF25Coordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]

type DreameSF25ConfigEntry = ConfigEntry[DreameSF25Coordinator]


async def async_setup_entry(hass: HomeAssistant, entry: DreameSF25ConfigEntry) -> bool:
    """Configura una entrada."""
    client = DreameSF25Client(
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_REGION, DEFAULT_REGION),
    )

    try:
        await hass.async_add_executor_job(client.resolve_device, entry.data.get(CONF_DID))
    except DreameAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except DreameApiError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = DreameSF25Coordinator(hass, entry, client)
    # contador de aperturas y modos virtuales (Remover / Compactar)
    await coordinator.modes.async_load()
    await coordinator.async_config_entry_first_refresh()
    # push en tiempo real; si falla, el coordinator sigue sondeando
    await coordinator.async_start_push()

    # al parar HA no se descargan las entradas: sin esto el hilo de paho sigue
    # vivo, bloquea el cierre y empuja datos a un loop ya cerrado
    async def _async_stop_push(_event: Event) -> None:
        await coordinator.async_stop_push()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop_push)
    )

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    return True


async def _async_update_options(hass: HomeAssistant, entry: DreameSF25ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: DreameSF25ConfigEntry) -> bool:
    """Descarga una entrada."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop_push()
        await entry.runtime_data.modes.async_unload()
    return unloaded
