"""Sensores binarios del Dreame SF25."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DreameSF25ConfigEntry
from .const import DOMAIN, PROP_LID, PROP_RUNNING, TARGET_MODEL
from .coordinator import DreameSF25Coordinator


@dataclass(frozen=True, kw_only=True)
class DreameSF25BinaryDescription(BinarySensorEntityDescription):
    """Descripcion de binary_sensor con clave MIoT (siid, piid)."""

    prop: tuple[int, int]
    # Como interpretar el valor crudo como on/off. Por defecto: distinto de 0.
    is_on_fn: Callable[[Any], bool] = lambda v: int(v) != 0


BINARY_SENSORS: tuple[DreameSF25BinaryDescription, ...] = (
    DreameSF25BinaryDescription(
        key="running",
        translation_key="running",
        name="Running",
        device_class=BinarySensorDeviceClass.RUNNING,
        prop=PROP_RUNNING,
        # 2.10 es tri-estado: -1=apagado, 0=pausa, 1=marcha -> "en marcha" solo si ==1
        is_on_fn=lambda v: int(v) == 1,
    ),
    DreameSF25BinaryDescription(
        key="lid",
        translation_key="lid",
        name="Lid",
        device_class=BinarySensorDeviceClass.OPENING,
        prop=PROP_LID,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DreameSF25ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crea los binary_sensors."""
    coordinator = entry.runtime_data
    async_add_entities(DreameSF25BinarySensor(coordinator, desc) for desc in BINARY_SENSORS)


class DreameSF25BinarySensor(CoordinatorEntity[DreameSF25Coordinator], BinarySensorEntity):
    """Binary sensor generico basado en una propiedad MIoT (0/1)."""

    entity_description: DreameSF25BinaryDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DreameSF25Coordinator,
        description: DreameSF25BinaryDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        did = coordinator.client.did
        self._attr_unique_id = f"{did}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(did))},
            manufacturer="Dreame",
            model=coordinator.client.model or TARGET_MODEL,
            name=coordinator.entry.title,
            sw_version=coordinator.client.firmware,
        )

    @property
    def is_on(self) -> bool | None:
        raw = self.coordinator.data.get(self.entity_description.prop)
        if raw is None:
            return None
        try:
            return self.entity_description.is_on_fn(raw)
        except (ValueError, TypeError):
            return None

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.entity_description.prop in (self.coordinator.data or {})
        )
