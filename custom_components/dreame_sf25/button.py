"""Botones (pausar / reanudar) del Dreame SF25.

Pausa y reanudar via la propiedad 2.10 (0=pausa, 1=reanudar). Confirmado en vivo.
Solo tienen efecto con un programa en marcha.
"""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DreameSF25ConfigEntry
from .const import (
    ACTION_WAKE,
    DOMAIN,
    PROP_LID,
    PROP_PROGRAM,
    PROP_RUNNING,
    PROP_STATUS,
    PROP_TEMPERATURE,
    TARGET_MODEL,
)
from .coordinator import DreameSF25Coordinator
from .control_guard import REMOTE_LID_MAX_TEMPERATURE_C, can_open_lid


@dataclass(frozen=True, kw_only=True)
class DreameSF25ButtonDescription(ButtonEntityDescription):
    """Descripcion de boton.

    - prop + value: escribe (siid,piid)=value al pulsarlo.
    - action: ejecuta una accion MIoT (siid, aiid) al pulsarlo.
    """

    prop: tuple[int, int] | None = None
    value: int | None = None
    action: tuple[int, int] | None = None


BUTTONS: tuple[DreameSF25ButtonDescription, ...] = (
    DreameSF25ButtonDescription(
        key="pause",
        translation_key="pause",
        name="Pause",
        icon="mdi:pause",
        prop=PROP_RUNNING,
        value=0,
    ),
    DreameSF25ButtonDescription(
        key="resume",
        translation_key="resume",
        name="Resume",
        icon="mdi:play",
        prop=PROP_RUNNING,
        value=1,
    ),
    DreameSF25ButtonDescription(
        key="open_lid",
        translation_key="open_lid",
        name="Open lid",
        icon="mdi:tray-arrow-up",
        prop=PROP_LID,
        value=1,
    ),
    DreameSF25ButtonDescription(
        key="wake",
        translation_key="wake",
        name="Wake",
        icon="mdi:sleep-off",
        action=ACTION_WAKE,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DreameSF25ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crea los botones."""
    coordinator = entry.runtime_data
    async_add_entities(DreameSF25Button(coordinator, desc) for desc in BUTTONS)


class DreameSF25Button(CoordinatorEntity[DreameSF25Coordinator], ButtonEntity):
    """Boton que escribe una propiedad MIoT al pulsarlo."""

    entity_description: DreameSF25ButtonDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DreameSF25Coordinator,
        description: DreameSF25ButtonDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        if description.key == "wake":
            self._attr_entity_registry_enabled_default = False
        did = coordinator.client.did
        self._attr_unique_id = f"{did}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(did))},
            manufacturer="Dreame",
            model=coordinator.client.model or TARGET_MODEL,
            name=coordinator.entry.title,
        )

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        data = self.coordinator.data or {}
        key = self.entity_description.key
        try:
            if key == "pause":
                return int(data[PROP_RUNNING]) == 1
            if key == "resume":
                return int(data[PROP_RUNNING]) == 0
            if key == "open_lid":
                return can_open_lid(data)
            if key == "wake":
                return int(data[PROP_STATUS]) == 3
        except (KeyError, TypeError, ValueError):
            return False
        return True

    async def async_press(self) -> None:
        desc = self.entity_description
        if desc.key in ("pause", "resume"):
            data = await self.hass.async_add_executor_job(
                self.coordinator.client.get_properties, [PROP_RUNNING]
            )
            try:
                running = int(data[PROP_RUNNING])
            except (KeyError, TypeError, ValueError) as err:
                raise HomeAssistantError("Cannot verify the SF25 running state") from err
            if desc.key == "pause" and running != 1:
                raise HomeAssistantError("The SF25 is not running")
            if desc.key == "resume" and running != 0:
                raise HomeAssistantError("The SF25 is not paused")
        if desc.key == "open_lid":
            # The coordinator may be up to five minutes behind while MQTT push
            # is healthy. Read the device again immediately before the write.
            data = await self.hass.async_add_executor_job(
                self.coordinator.client.get_properties,
                [PROP_PROGRAM, PROP_RUNNING, PROP_STATUS, PROP_TEMPERATURE],
            )
            if not can_open_lid(data):
                raise HomeAssistantError(
                    "The SF25 must be idle, stopped and at most "
                    f"{REMOTE_LID_MAX_TEMPERATURE_C:g} °C before HA opens the lid"
                )
        if desc.action is not None:
            siid, aiid = desc.action
            await self.hass.async_add_executor_job(
                self.coordinator.client.run_action, siid, aiid
            )
        else:
            siid, piid = desc.prop
            await self.hass.async_add_executor_job(
                self.coordinator.client.set_property, siid, piid, desc.value
            )
        await self.coordinator.async_request_refresh()
