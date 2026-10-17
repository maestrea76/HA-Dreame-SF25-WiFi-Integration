"""Modos virtuales del SF25: Remover y Compactar.

El aparato solo conoce ciclo (2.3=0) y autolimpieza (2.3=2). Aqui se construyen
dos modos propios sobre la autolimpieza, acotandola en el tiempo:

  - Remover   : autolimpieza durante 10 min.
  - Compactar : autolimpieza durante 1 hora.

Disparos automaticos:
  - Remover  : al cerrar la tapa, si se han acumulado >= 3 aperturas.
  - Compactar: a la hora configurada (por defecto 15:00), con >= 3 aperturas.

Contador de aperturas: se reinicia cuando termina de forma natural cualquier
programa propio del aparato (triturado, secado extra o autolimpieza). Cuenta
como final cualquier cambio de programa, no solo pasar a inactivo, porque tras
el triturado el aparato encadena a veces "secado extra" (2.3=1, ~2 h) y otras
veces no. Si se cancelan a medias, o si lo que corrio fue Remover o
Compactar, el contador se conserva.

Un programa se da por completado si se cumple cualquiera de tres criterios (ver
_completed): se vio la cuenta atras casi en cero, el aparato encadeno el secado
extra, o transcurrio practicamente toda su duracion. Con un solo criterio la
deteccion era fragil: perder contacto en el tramo final hacia que un ciclo
completo pareciera cancelado y el contador no se reiniciaba.

El estado (contador, modo virtual, vencimiento y programa en curso) se guarda en
disco para sobrevivir a un reinicio de Home Assistant.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.storage import Store

from .const import (
    CONF_AUTO_MODES,
    CONF_VIRTUAL_MODES,
    COMMAND_GRACE,
    DEFAULT_COMPACT_HOUR,
    DEFAULT_COMPACT_MINUTE,
    DOMAIN,
    LID_COUNT_THRESHOLD,
    NATURAL_END_MARGIN,
    NATURAL_END_REMAINING,
    PROGRAM_COMPACT,
    PROGRAM_MAP,
    PROGRAM_OPTIONS,
    PROGRAM_STIR,
    PROP_LID,
    PROP_PROGRAM,
    PROP_REMAINING_TIME,
    STORAGE_VERSION,
    VIRTUAL_DURATIONS,
)

if TYPE_CHECKING:
    from .coordinator import DreameSF25Coordinator

_LOGGER = logging.getLogger(__name__)

_PROGRAM_IDLE: int = PROGRAM_OPTIONS["idle"]
_PROGRAM_CYCLE: int = PROGRAM_OPTIONS["cycle"]
_PROGRAM_SELF_CLEAN: int = PROGRAM_OPTIONS["self_clean"]
_PROGRAM_EXTRA: int = PROGRAM_OPTIONS["extra"]
# programas del aparato cuyo final debe evaluarse para reiniciar el contador
_RUNNING_PROGRAMS: tuple[int, ...] = (_PROGRAM_CYCLE, _PROGRAM_EXTRA, _PROGRAM_SELF_CLEAN)


class DreameSF25Modes:
    """Contador de aperturas y modos virtuales (Remover / Compactar)."""

    def __init__(self, hass: HomeAssistant, coordinator: DreameSF25Coordinator, entry_id: str) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")

        self.lid_count: int = 0
        self.virtual_mode: str | None = None
        self._virtual_until: float = 0.0
        # hora del disparo diario de Compactar (editable desde HA)
        self.compact_hour: int = DEFAULT_COMPACT_HOUR
        self.compact_minute: int = DEFAULT_COMPACT_MINUTE

        # True mientras el programa que corre en el aparato lo lanzamos nosotros
        # (Remover/Compactar). Sirve para no confundir su final con el de un
        # programa real y, por tanto, no tocar el contador.
        self._owns_program: bool = False
        # valor de 2.11 que reporto el aparato al arrancar el modo virtual
        # (la autolimpieza empieza en ~90 min); sirve para deducir lo transcurrido
        self._virtual_start_remaining: int | None = None
        # instante de la ultima orden enviada: durante unos segundos las lecturas
        # pueden venir desfasadas (el aparato aun no ha arrancado/parado)
        self._command_at: float = 0.0

        self._last_lid: int | None = None
        self._last_program: int | None = None
        self._last_remaining: int | None = None
        # seguimiento del programa que corre en el aparato, para saber si acabo
        # de verdad aunque perdamos contacto cerca del final. Se persiste:
        # {"program", "started_at", "total", "min_remaining"}
        self._prog_run: dict | None = None

        self._unsub_timer = None
        self._unsub_daily = None

    @property
    def virtual_remaining_minutes(self) -> int | None:
        """Minutos que faltan del modo virtual, deducidos del contador del aparato.

        El aparato informa en 2.11 el tiempo de SU autolimpieza (~90 min), no el
        del modo acotado. Calculamos:
            transcurrido = valor_al_arrancar - valor_actual
            restante     = duracion_del_modo - transcurrido
        """
        if self.virtual_mode is None or self._virtual_start_remaining is None:
            return None
        current = _as_int((self.coordinator.data or {}).get(PROP_REMAINING_TIME))
        if current is None:
            return None
        elapsed = self._virtual_start_remaining - current
        total = VIRTUAL_DURATIONS[self.virtual_mode] // 60
        return max(0, total - elapsed)

    # ------------------------------------------------------------ ciclo de vida
    async def async_load(self) -> None:
        """Restaura el estado guardado y reprograma lo pendiente."""
        data = await self._store.async_load() or {}
        self.lid_count = int(data.get("lid_count", 0))
        self.virtual_mode = data.get("virtual_mode")
        self._owns_program = self.virtual_mode is not None
        self._virtual_until = float(data.get("virtual_until", 0) or 0)
        self.compact_hour = int(data.get("compact_hour", DEFAULT_COMPACT_HOUR))
        self.compact_minute = int(data.get("compact_minute", DEFAULT_COMPACT_MINUTE))
        self._prog_run = data.get("prog_run")
        self._last_program = data.get("last_program")

        self._schedule_daily()

        if self.virtual_mode:
            remaining = self._virtual_until - time.time()
            if remaining > 0:
                _LOGGER.info(
                    "Reanudando modo %s tras reinicio (%.0f s restantes)",
                    self.virtual_mode,
                    remaining,
                )
                self._schedule_expiry(remaining)
            else:
                # Do not write before the first refresh has confirmed what is running.
                _LOGGER.info("El modo %s vencio durante el reinicio; verificando estado", self.virtual_mode)
                self._schedule_expiry(1)

    async def async_unload(self) -> None:
        self._cancel_timer()
        if self._unsub_daily is not None:
            self._unsub_daily()
            self._unsub_daily = None
        await self._async_save()

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "lid_count": self.lid_count,
                "virtual_mode": self.virtual_mode,
                "virtual_until": self._virtual_until,
                "compact_hour": self.compact_hour,
                "compact_minute": self.compact_minute,
                # asi un reinicio de HA no pierde el programa en curso
                "prog_run": self._prog_run,
                "last_program": self._last_program,
            }
        )

    @callback
    def _schedule_daily(self) -> None:
        """(Re)programa el disparo diario de Compactar a la hora configurada."""
        if self._unsub_daily is not None:
            self._unsub_daily()
        self._unsub_daily = async_track_time_change(
            self.hass,
            self._async_daily_compact,
            hour=self.compact_hour,
            minute=self.compact_minute,
            second=0,
        )
        _LOGGER.debug("Compactar programado a las %02d:%02d", self.compact_hour, self.compact_minute)

    async def async_set_compact_time(self, hour: int, minute: int) -> None:
        """Cambia la hora del disparo diario de Compactar."""
        self.compact_hour = int(hour)
        self.compact_minute = int(minute)
        self._schedule_daily()
        await self._async_save()

    async def async_set_lid_count(self, value: int) -> None:
        """Fija el contador de aperturas (editable desde HA)."""
        self.lid_count = max(0, int(value))
        await self._async_save()
        self.coordinator.async_update_listeners()

    # --------------------------------------------------------------- temporizador
    @callback
    def _cancel_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    @callback
    def _schedule_expiry(self, delay: float) -> None:
        self._cancel_timer()
        self._unsub_timer = async_call_later(self.hass, delay, self._async_expired)

    async def _async_expired(self, _now) -> None:
        _LOGGER.info("Modo %s completado; parando el aparato", self.virtual_mode)
        try:
            await self._async_finish_virtual()
        except Exception:
            _LOGGER.exception("No se pudo confirmar la parada del modo virtual; reintentando")
            self._schedule_expiry(60)

    async def _async_finish_virtual(self) -> None:
        """Detiene el aparato y cierra el modo virtual.

        Verifica que el aparato siga en nuestra autolimpieza antes de pararlo.
        """
        # A user may have switched programs from the Dreame app while HA was
        # restarting. Only stop a self-clean cycle that we still own.
        await self.coordinator.async_request_refresh()
        program = _as_int((self.coordinator.data or {}).get(PROP_PROGRAM))
        if program is None:
            raise HomeAssistantError("No se pudo verificar el programa activo")
        if self._owns_program and program == _PROGRAM_SELF_CLEAN:
            await self._async_set_program(_PROGRAM_IDLE)
        self._cancel_timer()
        self.virtual_mode = None
        self._virtual_until = 0.0
        self._virtual_start_remaining = None
        self._owns_program = False
        await self._async_save()
        await self.coordinator.async_request_refresh()

    # -------------------------------------------------------------------- ordenes
    async def _async_set_program(self, value: int) -> None:
        siid, piid = PROP_PROGRAM
        await self.hass.async_add_executor_job(
            self.coordinator.client.set_property, siid, piid, value
        )
        self._command_at = time.time()

    async def async_start_virtual(self, mode: str) -> None:
        """Arranca Remover o Compactar (autolimpieza acotada)."""
        if not self.coordinator.entry.options.get(CONF_VIRTUAL_MODES, False):
            raise HomeAssistantError("Virtual modes are disabled in the integration options")
        await self.coordinator.async_request_refresh()
        program = _as_int((self.coordinator.data or {}).get(PROP_PROGRAM))
        if program != _PROGRAM_IDLE:
            raise HomeAssistantError("The SF25 must be idle before starting a virtual mode")
        duration = VIRTUAL_DURATIONS[mode]
        await self._async_set_program(_PROGRAM_SELF_CLEAN)
        self._owns_program = True
        self._virtual_start_remaining = None
        self.virtual_mode = mode
        self._virtual_until = time.time() + duration
        self._schedule_expiry(duration)
        await self._async_save()
        _LOGGER.info("Modo %s iniciado (%s min)", mode, duration // 60)
        await self.coordinator.async_request_refresh()

    async def async_clear_virtual(self) -> None:
        """Olvida el modo virtual sin tocar el aparato (cambio manual de modo)."""
        if self.virtual_mode is None:
            return
        self._cancel_timer()
        self.virtual_mode = None
        self._virtual_until = 0.0
        self._virtual_start_remaining = None
        self._owns_program = False
        await self._async_save()

    async def async_reset_counter(self) -> None:
        self.lid_count = 0
        await self._async_save()
        self.coordinator.async_update_listeners()

    # ------------------------------------------------------------- observador
    @callback
    def handle_update(self, data: dict[tuple[int, int], Any]) -> None:
        """Analiza cada actualizacion para contar aperturas y detectar finales."""
        lid = _as_int(data.get(PROP_LID))
        program = _as_int(data.get(PROP_PROGRAM))
        remaining = _as_int(data.get(PROP_REMAINING_TIME))

        # --- fin de programa: decidir si reiniciar el contador ---
        # Se evalua ANTES de refrescar _last_remaining, porque al encadenar
        # triturado -> secado extra el aparato ya informa el tiempo de la fase
        # nueva y perderiamos el ultimo valor del programa que acaba de terminar.
        # Cuenta como final cualquier cambio de programa (no solo pasar a
        # inactivo): tras el triturado no siempre viene el secado extra.
        if (
            self._last_program is not None
            and program is not None
            and program != self._last_program
            and self._last_program in _RUNNING_PROGRAMS
            # tras enviar una orden las lecturas pueden venir desfasadas: no
            # damos por terminado un programa que quiza ni siquiera ha arrancado
            and (time.time() - self._command_at) > COMMAND_GRACE
        ):
            self.hass.async_create_task(
                self._async_program_ended(self._last_program, self._prog_run, program)
            )

        # --- seguimiento del programa en curso ---
        if program is not None and program != _PROGRAM_IDLE:
            run = self._prog_run
            if run is None or run.get("program") != program:
                self._prog_run = {
                    "program": program,
                    "started_at": time.time(),
                    "total": remaining,
                    "min_remaining": remaining,
                }
            elif remaining is not None:
                if run.get("total") is None or remaining > run["total"]:
                    run["total"] = remaining
                if run.get("min_remaining") is None or remaining < run["min_remaining"]:
                    run["min_remaining"] = remaining
        elif program == _PROGRAM_IDLE:
            self._prog_run = None

        if program is not None and program != _PROGRAM_IDLE and remaining is not None:
            self._last_remaining = remaining
            # primer valor util tras arrancar un modo virtual: es la referencia
            if (
                self.virtual_mode is not None
                and self._virtual_start_remaining is None
                and remaining > 0
            ):
                self._virtual_start_remaining = remaining
                _LOGGER.debug(
                    "%s: el aparato arranca con %s min; se mostraran los del modo",
                    self.virtual_mode, remaining,
                )
        elif program == _PROGRAM_IDLE:
            self._last_remaining = None

        # --- tapa: contar cierre tras apertura ---
        if lid is not None:
            if self._last_lid == 1 and lid == 0:
                self.hass.async_create_task(self._async_lid_closed(program))
            self._last_lid = lid

        if program is not None:
            self._last_program = program

    async def _async_program_ended(
        self, ended: int, run: dict | None, new_program: int | None
    ) -> None:
        """Un programa del aparato acaba de terminar.

        `ended` es el programa que termina, `run` su seguimiento (arranque,
        duracion y minimo visto) y `new_program` al que ha pasado.
        """
        if self.virtual_mode is not None or self._owns_program:
            # Remover/Compactar: NUNCA reinician el contador. Si el aparato paro
            # por su cuenta antes de tiempo, cerramos tambien el modo virtual.
            self._owns_program = False
            if self.virtual_mode is not None:
                _LOGGER.info(
                    "%s terminado antes de tiempo por el aparato; contador intacto (%s)",
                    self.virtual_mode, self.lid_count,
                )
                self._cancel_timer()
                self.virtual_mode = None
                self._virtual_until = 0.0
                self._virtual_start_remaining = None
            else:
                _LOGGER.debug("Fin de un modo virtual; contador intacto (%s)", self.lid_count)
            await self._async_save()
            return

        name = PROGRAM_MAP.get(ended, ended)
        natural, reason = self._completed(ended, run, new_program)
        if natural:
            _LOGGER.info(
                "Programa %s completado (%s); contador de aperturas a cero", name, reason
            )
            await self.async_reset_counter()
        else:
            _LOGGER.info(
                "Programa %s interrumpido (%s); se conserva el contador (%s)",
                name, reason, self.lid_count,
            )

    def _completed(
        self, ended: int, run: dict | None, new_program: int | None
    ) -> tuple[bool, str]:
        """Decide si el programa llego a completarse. Devuelve (si, motivo).

        Tres criterios independientes: basta con uno. Antes solo existia el
        primero, y perder contacto con el aparato en el tramo final hacia que
        un ciclo completo pareciera cancelado.
        """
        # 1) el aparato encadeno el secado extra: solo lo hace tras completar
        if ended == _PROGRAM_CYCLE and new_program == _PROGRAM_EXTRA:
            return True, "el aparato encadeno el secado extra"

        if not run:
            return False, "sin datos del programa"

        # 2) llegamos a ver la cuenta atras casi en cero
        min_rem = run.get("min_remaining")
        if min_rem is not None and min_rem <= NATURAL_END_REMAINING:
            return True, f"la cuenta atras llego a {min_rem} min"

        # 3) transcurrio practicamente toda su duracion, aunque no vieramos el final
        total = run.get("total")
        started = run.get("started_at")
        if total and started:
            elapsed = (time.time() - started) / 60
            if elapsed >= total - NATURAL_END_MARGIN:
                return True, f"transcurrieron {elapsed:.0f} de {total} min"
            return False, f"solo {elapsed:.0f} de {total} min"

        return False, f"minimo visto {min_rem} min"

    async def _async_lid_closed(self, program: int | None) -> None:
        """La tapa se acaba de cerrar."""
        self.lid_count += 1
        await self._async_save()
        self.coordinator.async_update_listeners()
        _LOGGER.debug("Tapa cerrada; aperturas acumuladas: %s", self.lid_count)

        if not (
            self.coordinator.entry.options.get(CONF_AUTO_MODES, False)
            and self.coordinator.entry.options.get(CONF_VIRTUAL_MODES, False)
        ):
            return

        if self.virtual_mode is not None:
            return  # ya estamos removiendo/compactando: no reiniciar ni cancelar
        if program != _PROGRAM_IDLE:
            return  # hay un programa en marcha: no interrumpimos
        if self.lid_count >= LID_COUNT_THRESHOLD:
            _LOGGER.info(
                "Tapa cerrada con %s aperturas acumuladas: iniciando Remover",
                self.lid_count,
            )
            await self.async_start_virtual(PROGRAM_STIR)

    async def _async_daily_compact(self, _now) -> None:
        """Disparo diario de Compactar a la hora configurada."""
        if not (
            self.coordinator.entry.options.get(CONF_AUTO_MODES, False)
            and self.coordinator.entry.options.get(CONF_VIRTUAL_MODES, False)
        ):
            return
        if self.lid_count < LID_COUNT_THRESHOLD:
            return
        if self.virtual_mode is not None:
            return
        program = _as_int((self.coordinator.data or {}).get(PROP_PROGRAM))
        if program != _PROGRAM_IDLE:
            _LOGGER.info(
                "%02d:%02d con %s aperturas, pero hay un programa en marcha: se omite Compactar",
                self.compact_hour,
                self.compact_minute,
                self.lid_count,
            )
            return
        _LOGGER.info("Disparo diario con %s aperturas acumuladas: iniciando Compactar", self.lid_count)
        await self.async_start_virtual(PROGRAM_COMPACT)


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (ValueError, TypeError):
        return None
