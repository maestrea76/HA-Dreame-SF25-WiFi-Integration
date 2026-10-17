"""Switches (controles escribibles) del Dreame SF25."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import (
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DreameSF25ConfigEntry
from .api import DreameApiError
from .const import DOMAIN, PROP_CHILD_LOCK, PROP_SILENT_MODE, TARGET_MODEL
from .coordinator import DreameSF25Coordinator


@dataclass(frozen=True, kw_only=True)
class DreameSF25SwitchDescription(SwitchEntityDescription):
    """Descripcion de switch con clave MIoT (siid, piid)."""

    prop: tuple[int, int]


SWITCHES: tuple[DreameSF25SwitchDescription, ...] = (
    DreameSF25SwitchDescription(
        key="child_lock",
        translation_key="child_lock",
        name="Child lock",
        icon="mdi:lock",
        prop=PROP_CHILD_LOCK,
    ),
    DreameSF25SwitchDescription(
        key="silent_mode",
        translation_key="silent_mode",
        name="Silent mode",
        icon="mdi:volume-off",
        prop=PROP_SILENT_MODE,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DreameSF25ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crea los switches."""
    coordinator = entry.runtime_data
    async_add_entities(DreameSF25Switch(coordinator, desc) for desc in SWITCHES)


class DreameSF25Switch(CoordinatorEntity[DreameSF25Coordinator], SwitchEntity):
    """Switch generico basado en una propiedad MIoT booleana (0/1)."""

    entity_description: DreameSF25SwitchDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DreameSF25Coordinator,
        description: DreameSF25SwitchDescription,
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
        )

    @property
    def is_on(self) -> bool | None:
        raw = self.coordinator.data.get(self.entity_description.prop)
        if raw is None:
            return None
        return bool(int(raw))

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.entity_description.prop in (self.coordinator.data or {})
        )

    async def _async_set(self, value: int) -> None:
        siid, piid = self.entity_description.prop
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.client.set_property, siid, piid, value
            )
        except DreameApiError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(0)
