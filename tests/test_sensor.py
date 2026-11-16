import pytest
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er


async def test_sensor_metadata_and_values(hass, setup_entry):
    air = hass.states.get("sensor.pellet_stove_air_temperature")
    assert air.state == "21.5"
    assert air.attributes["unit_of_measurement"] == "°C"
    assert air.attributes["device_class"] == "temperature"
    assert air.attributes["state_class"] == "measurement"
    assert hass.states.get("sensor.pellet_stove_power_state").state == "on"
    assert (
        hass.states.get("sensor.pellet_stove_fan_speed").attributes[
            "unit_of_measurement"
        ]
        == "rpm"
    )
    hours = hass.states.get("sensor.pellet_stove_total_hours")
    assert hours.attributes["device_class"] == "duration"
    assert hours.attributes["state_class"] == "total_increasing"


@pytest.mark.parametrize(
    "key", ["last_alarm", "total_hours", "partial_hours", "ignition_count"]
)
async def test_diagnostic_sensor_category(hass, setup_entry, key):
    entity = er.async_get(hass).async_get(f"sensor.pellet_stove_{key}")
    assert entity.entity_category is EntityCategory.DIAGNOSTIC
