"""Hora del disparo diario de Compactar (editable desde HA)."""
from __future__ import annotations

from datetime import time as dt_time

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DreameSF25ConfigEntry
from .const import DOMAIN, TARGET_MODEL
from .coordinator import DreameSF25Coordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DreameSF25ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([DreameSF25CompactTime(entry.runtime_data)])


class DreameSF25CompactTime(CoordinatorEntity[DreameSF25Coordinator], TimeEntity):
    """Hora a la que se lanza Compactar cada dia (si hay aperturas suficientes)."""

    _attr_has_entity_name = True
    _attr_translation_key = "compact_time"
    _attr_icon = "mdi:clock-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DreameSF25Coordinator) -> None:
        super().__init__(coordinator)
        did = coordinator.client.did
        self._attr_unique_id = f"{did}_compact_time"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(did))},
            manufacturer="Dreame",
            model=coordinator.client.model or TARGET_MODEL,
            name=coordinator.entry.title,
            sw_version=coordinator.client.firmware,
        )

    @property
    def native_value(self) -> dt_time:
        modes = self.coordinator.modes
        return dt_time(hour=modes.compact_hour, minute=modes.compact_minute)

    async def async_set_value(self, value: dt_time) -> None:
        await self.coordinator.modes.async_set_compact_time(value.hour, value.minute)
        self.async_write_ha_state()
