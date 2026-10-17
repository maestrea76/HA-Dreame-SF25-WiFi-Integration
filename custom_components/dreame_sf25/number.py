"""Contador de aperturas de tapa (editable desde HA).

Es la variable que gobierna los disparos automaticos de Remover y Compactar.
Al ser una entidad `number`, se puede leer, ajustar a mano y usar en
automatizaciones (por ejemplo, ponerla a 0 desde un script).
"""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DreameSF25ConfigEntry
from .const import DOMAIN, LID_COUNT_MAX, TARGET_MODEL
from .coordinator import DreameSF25Coordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DreameSF25ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([DreameSF25LidCount(entry.runtime_data)])


class DreameSF25LidCount(CoordinatorEntity[DreameSF25Coordinator], NumberEntity):
    """Aperturas de tapa acumuladas desde el ultimo procesado."""

    _attr_has_entity_name = True
    _attr_translation_key = "lid_count"
    _attr_icon = "mdi:counter"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = LID_COUNT_MAX
    _attr_native_step = 1

    def __init__(self, coordinator: DreameSF25Coordinator) -> None:
        super().__init__(coordinator)
        did = coordinator.client.did
        self._attr_unique_id = f"{did}_lid_count"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(did))},
            manufacturer="Dreame",
            model=coordinator.client.model or TARGET_MODEL,
            name=coordinator.entry.title,
        )

    @property
    def native_value(self) -> float:
        return float(self.coordinator.modes.lid_count)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.modes.async_set_lid_count(int(value))
        self.async_write_ha_state()
