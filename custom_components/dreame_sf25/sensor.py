"""Sensores del Dreame SF25."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DreameSF25ConfigEntry
from .const import (
    DOMAIN,
    PROP_CARBON_FILTER_DAYS,
    PROP_CARBON_FILTER_PCT,
    PROP_ENERGY_WH,
    PROP_HUMIDITY,
    PROP_REMAINING_TIME,
    PROP_STATUS,
    PROP_TEMPERATURE,
    STATUS_MAP,
    TARGET_MODEL,
)
from .coordinator import DreameSF25Coordinator


def _map_status(v: Any) -> str:
    try:
        return STATUS_MAP.get(int(v), f"unknown_{v}")
    except (ValueError, TypeError):
        return f"unknown_{v}"


def _remaining_minutes(coordinator) -> int | None:
    """Minutos restantes de lo que realmente esta corriendo.

    En Remover/Compactar el aparato informa el tiempo de SU autolimpieza, no el
    del modo acotado; en ese caso mostramos el del modo.
    """
    virtual = coordinator.modes.virtual_remaining_minutes
    if virtual is not None:
        return virtual
    raw = (coordinator.data or {}).get(PROP_REMAINING_TIME)
    try:
        minutes = int(raw)
        return minutes if minutes >= 0 else None
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True, kw_only=True)
class DreameSF25SensorDescription(SensorEntityDescription):
    """Descripcion de sensor con clave MIoT y conversion.

    - prop + value_fn: sensor basado en una sola propiedad (siid, piid).
    - compute_fn: sensor calculado a partir de TODO el diccionario de datos.
    - attrs_fn: atributos extra a partir del valor crudo de la propiedad.
    """

    prop: tuple[int, int] | None = None
    value_fn: Callable[[Any], Any] = lambda v: v
    compute_fn: Callable[[dict[tuple[int, int], Any]], Any] | None = None
    attrs_fn: Callable[[Any], dict] | None = None
    # valor/atributos calculados a partir del coordinator (no de una sola prop)
    coord_fn: Callable[[Any], Any] | None = None
    coord_attrs_fn: Callable[[Any], dict | None] | None = None


SENSORS: tuple[DreameSF25SensorDescription, ...] = (
    DreameSF25SensorDescription(
        key="status",
        translation_key="status",
        name="Status",
        icon="mdi:state-machine",
        prop=PROP_STATUS,
        value_fn=_map_status,
    ),
    DreameSF25SensorDescription(
        key="remaining_time",
        translation_key="remaining_time",
        name="Remaining time",
        icon="mdi:timer-sand",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        prop=PROP_REMAINING_TIME,
        coord_fn=_remaining_minutes,
    ),
    DreameSF25SensorDescription(
        key="energy",
        translation_key="energy",
        name="Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        prop=PROP_ENERGY_WH,
        value_fn=lambda v: round(int(v) / 1000, 3),  # Wh crudos -> kWh
    ),
    DreameSF25SensorDescription(
        key="carbon_filter_pct",
        translation_key="carbon_filter_pct",
        name="Carbon filter life",
        icon="mdi:air-filter",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        prop=PROP_CARBON_FILTER_PCT,
    ),
    DreameSF25SensorDescription(
        key="carbon_filter_days",
        translation_key="carbon_filter_days",
        name="Carbon filter remaining",
        icon="mdi:air-filter",
        native_unit_of_measurement=UnitOfTime.DAYS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        prop=PROP_CARBON_FILTER_DAYS,
    ),
    DreameSF25SensorDescription(
        key="temperature",
        translation_key="temperature",
        name="Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        prop=PROP_TEMPERATURE,
    ),
    DreameSF25SensorDescription(
        key="humidity",
        translation_key="humidity",
        name="Humidity",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        prop=PROP_HUMIDITY,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DreameSF25ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crea los sensores."""
    coordinator = entry.runtime_data
    async_add_entities(DreameSF25Sensor(coordinator, desc) for desc in SENSORS)


class DreameSF25Sensor(CoordinatorEntity[DreameSF25Coordinator], SensorEntity):
    """Sensor generico basado en una propiedad MIoT."""

    entity_description: DreameSF25SensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DreameSF25Coordinator,
        description: DreameSF25SensorDescription,
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
    def native_value(self) -> Any:
        if self.entity_description.coord_fn is not None:
            return self.entity_description.coord_fn(self.coordinator)
        data = self.coordinator.data or {}
        if self.entity_description.compute_fn is not None:
            return self.entity_description.compute_fn(data)
        raw = data.get(self.entity_description.prop)
        if raw is None:
            return None
        try:
            return self.entity_description.value_fn(raw)
        except (ValueError, TypeError):
            return None

    @property
    def extra_state_attributes(self) -> dict | None:
        desc = self.entity_description
        if desc.coord_attrs_fn is not None:
            return desc.coord_attrs_fn(self.coordinator)
        if desc.attrs_fn is None or desc.prop is None:
            return None
        raw = (self.coordinator.data or {}).get(desc.prop)
        if raw is None:
            return None
        try:
            return desc.attrs_fn(raw)
        except (ValueError, TypeError):
            return None

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        if self.entity_description.compute_fn is not None:
            return True
        return self.entity_description.prop in (self.coordinator.data or {})
