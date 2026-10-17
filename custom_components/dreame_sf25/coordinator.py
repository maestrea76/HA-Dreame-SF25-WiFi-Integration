"""DataUpdateCoordinator para el Dreame SF25.

Estrategia hibrida:
  - MQTT (push): el dispositivo empuja los cambios al instante.
  - Sondeo: red de seguridad. Lento cuando el push esta vivo; rapido si se cae.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DreameApiError, DreameAuthError, DreameSF25Client
from .const import (
    CONF_SAFETY_STOP,
    EVENT_SAFETY_STOP,
    POLL_PROPERTIES,
    PROGRAM_OPTIONS,
    PROP_PROGRAM,
    PROP_TEMPERATURE,
    SAFETY_TEMP_LIMITS,
    SAFETY_TEMP_LIMITS_VIRTUAL,
    SCAN_INTERVAL_POLL,
    SCAN_INTERVAL_PUSH,
)
from .modes import DreameSF25Modes
from .mqtt import DreameSF25Mqtt

_LOGGER = logging.getLogger(__name__)

# El RPC de sondeo tarda varios segundos. Si un push MQTT llega mientras el
# sondeo esta en vuelo, no dejamos que el resultado viejo pise el valor fresco
# (p.ej. tapa abierta).
_PUSH_TTL = 20.0


class DreameSF25Coordinator(DataUpdateCoordinator[dict[tuple[int, int], Any]]):
    """Mantiene el estado del SF25 (push MQTT + sondeo de respaldo)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: DreameSF25Client) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Dreame SF25",
            update_interval=timedelta(seconds=SCAN_INTERVAL_POLL),
        )
        self.entry = entry
        self.client = client
        self.mqtt = DreameSF25Mqtt(client, self._handle_push)
        # evita repetir la parada mientras la temperatura siga alta
        self._safety_tripped = False
        self._push_at: dict[tuple[int, int], float] = {}
        self.modes = DreameSF25Modes(hass, self, entry.entry_id)

    # ------------------------------------------------------------------- push
    async def async_start_push(self) -> None:
        """Arranca el cliente MQTT. Si falla, seguimos con sondeo rapido."""
        try:
            await self.hass.async_add_executor_job(self.mqtt.start)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "No se pudo iniciar el push MQTT (%s); se usara solo sondeo cada %ss",
                err,
                SCAN_INTERVAL_POLL,
            )

    async def async_stop_push(self) -> None:
        await self.hass.async_add_executor_job(self.mqtt.stop)

    def _handle_push(self, updates: dict[tuple[int, int], Any]) -> None:
        """Llamado desde el hilo de paho: lo pasamos al loop de HA."""
        # MQTT may deliver one final packet while HA is shutting down. In that
        # narrow window the loop can close between the check and scheduling.
        if self.hass.loop.is_closed():
            return
        try:
            self.hass.loop.call_soon_threadsafe(self._apply_push, updates)
        except RuntimeError:
            if not self.hass.loop.is_closed():
                raise

    @callback
    def _apply_push(self, updates: dict[tuple[int, int], Any]) -> None:
        now = time.monotonic()
        for key in updates:
            self._push_at[key] = now
        data = dict(self.data or {})
        data.update(updates)
        self._sync_interval()
        self._check_safety(data)
        self.modes.handle_update(data)
        self.async_set_updated_data(data)

    # --------------------------------------------------------------- seguridad
    @callback
    def _check_safety(self, data: dict[tuple[int, int], Any]) -> None:
        """Para el aparato si la temperatura supera el limite del programa.

        Proteccion SECUNDARIA: depende de HA y de la nube, no sustituye al
        corte termico del propio aparato.
        """
        if not self.entry.options.get(CONF_SAFETY_STOP, False):
            return
        raw_temp = data.get(PROP_TEMPERATURE)
        raw_prog = data.get(PROP_PROGRAM)
        if raw_temp is None or raw_prog is None:
            return
        try:
            temp = float(raw_temp)
            program = int(raw_prog)
        except (ValueError, TypeError):
            return

        # Remover/Compactar se ejecutan como autolimpieza: hay que mirar el
        # modo virtual para saber que limite aplicar.
        virtual = self.modes.virtual_mode
        if virtual is not None and program in SAFETY_TEMP_LIMITS:
            limit = SAFETY_TEMP_LIMITS_VIRTUAL.get(virtual)
        else:
            limit = SAFETY_TEMP_LIMITS.get(program)
        if limit is None or temp <= limit:
            # sin programa activo, o temperatura normal: rearmamos
            self._safety_tripped = False
            return

        if self._safety_tripped:
            return  # ya se ordeno la parada; no repetimos
        self._safety_tripped = True

        _LOGGER.error(
            "PARADA DE SEGURIDAD: %.1f C supera el limite de %s C (%s); "
            "deteniendo el Dreame SF25",
            temp,
            limit,
            virtual or f"programa {program}",
        )
        self.hass.async_create_task(self._async_safety_stop(temp, limit, program))

    async def _async_safety_stop(self, temp: float, limit: int, program: int) -> None:
        """Envia la orden de parada, con reintentos, y avisa mediante un evento."""
        siid, piid = PROP_PROGRAM
        stopped = False
        for attempt in (1, 2, 3):
            try:
                await self.hass.async_add_executor_job(
                    self.client.set_property, siid, piid, PROGRAM_OPTIONS["idle"]
                )
                stopped = True
                break
            except Exception as err:  # noqa: BLE001
                _LOGGER.error(
                    "Parada de seguridad: intento %s fallido (%s)", attempt, err
                )

        self.hass.bus.async_fire(
            EVENT_SAFETY_STOP,
            {
                "device_id": self.client.did,
                "temperature": temp,
                "limit": limit,
                "program": program,
                "stopped": stopped,
            },
        )
        if not stopped:
            _LOGGER.error(
                "PARADA DE SEGURIDAD NO CONFIRMADA: no se pudo detener el aparato. "
                "Desconectalo manualmente si la temperatura sigue alta."
            )
        await self.async_request_refresh()

    # ----------------------------------------------------------------- sondeo
    @callback
    def _sync_interval(self) -> None:
        """Sondeo lento mientras el push este vivo; rapido si se cae."""
        wanted = timedelta(
            seconds=SCAN_INTERVAL_PUSH if self.mqtt.connected else SCAN_INTERVAL_POLL
        )
        if self.update_interval != wanted:
            self.update_interval = wanted

    async def _async_update_data(self) -> dict[tuple[int, int], Any]:
        try:
            data = await self.hass.async_add_executor_job(
                self.client.get_properties, POLL_PROPERTIES
            )
        except DreameAuthError as err:
            raise UpdateFailed(f"Autenticacion rechazada: {err}") from err
        except DreameApiError as err:
            raise UpdateFailed(f"Error de la nube Dreame: {err}") from err

        self._sync_interval()
        # el sondeo confirma el estado completo; el push solo trae lo que cambia
        merged = dict(self.data or {})
        now = time.monotonic()
        for key, value in data.items():
            if now - self._push_at.get(key, 0.0) < _PUSH_TTL:
                continue
            merged[key] = value
        self._check_safety(merged)
        self.modes.handle_update(merged)
        return merged
