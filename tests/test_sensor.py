from dataclasses import replace

from aiopelletstove import PowerState


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


async def test_off_values_from_library_reach_sensors(
    hass, setup_entry, mock_stove, stove_state
):
    mock_stove.async_get_state.return_value = replace(
        stove_state,
        power_state=PowerState.OFF,
        current_power=0,
        flue_temperature=0,
        fan_speed=0,
    )
    await setup_entry.runtime_data.async_refresh()
    for key in ("current_power", "flue_temperature", "fan_speed"):
        assert hass.states.get(f"sensor.pellet_stove_{key}").state == "0"
