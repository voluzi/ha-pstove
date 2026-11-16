"""Status and diagnostic sensors."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PstoveConfigEntry
from .entity import PstoveEntity

SENSORS = (
    SensorEntityDescription(
        key="power_state",
        name="Power state",
        icon="mdi:power",
    ),
    SensorEntityDescription(
        key="air_temperature",
        name="Air temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="flue_temperature",
        name="Flue temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="current_power",
        name="Current power",
        icon="mdi:flash",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="fan_speed",
        name="Fan speed",
        icon="mdi:fan",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="rpm",
    ),
    SensorEntityDescription(
        key="last_alarm",
        name="Last alarm",
        icon="mdi:alert-circle",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="ignition_count",
        name="Ignition count",
        icon="mdi:fire",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="total_hours",
        name="Total hours",
        icon="mdi:clock-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
    ),
    SensorEntityDescription(
        key="partial_hours",
        name="Partial hours",
        icon="mdi:clock-start",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PstoveConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(PstoveSensor(entry, description) for description in SENSORS)


class PstoveSensor(PstoveEntity, SensorEntity):
    def __init__(
        self, entry: PstoveConfigEntry, description: SensorEntityDescription
    ) -> None:
        super().__init__(entry, description.key, str(description.name))
        self.entity_description = description

    @property
    def native_value(self) -> int | float | str | None:
        if self.entity_description.key == "power_state":
            return self.coordinator.data.power_state.label
        value: int | float | None = getattr(
            self.coordinator.data, self.entity_description.key
        )
        return value

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None
