from dataclasses import replace

import pytest
from aiopelletstove import PelletStoveResponseError, PowerState
from aiopelletstove.registers import POWER_LEVEL, TARGET_TEMPERATURE
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError


async def test_climate_controls(hass, setup_entry, mock_stove):
    entity_id = "climate.pellet_stove_thermostat"
    state = hass.states.get(entity_id)
    assert state.state == "heat"
    assert state.attributes["hvac_action"] == "heating"
    assert state.attributes["temperature"] == 23
    assert state.attributes["current_temperature"] == 21.5
    assert state.attributes["preset_mode"] == "Level 3"
    assert state.attributes["min_temp"] == 7
    assert state.attributes["max_temp"] == 40
    for service, data, register, value in [
        ("set_temperature", {"temperature": 24}, TARGET_TEMPERATURE, 24),
        ("set_preset_mode", {"preset_mode": "Level 4"}, POWER_LEVEL, 4),
    ]:
        await hass.services.async_call(
            "climate", service, {"entity_id": entity_id, **data}, blocking=True
        )
        mock_stove.async_write.assert_awaited_with(register, value)
    for service, value in [("turn_off", False), ("turn_on", True)]:
        await hass.services.async_call(
            "climate", service, {"entity_id": entity_id}, blocking=True
        )
        mock_stove.async_set_power.assert_awaited_with(value)


@pytest.mark.parametrize(
    "power,current,mode,action",
    [
        (PowerState.RUNNING, 0, "heat", "idle"),
        (PowerState.LIGHTING, 0, "heat", "heating"),
        (PowerState.ASHPAN_CLEANING, 1, "heat", "heating"),
        (PowerState.FINAL_CLEANING, 0, "off", "off"),
        (PowerState.STANDBY, 0, "off", "off"),
        (PowerState.OFF, 0, "off", "off"),
    ],
)
async def test_climate_state_mapping(
    hass, setup_entry, mock_stove, stove_state, power, current, mode, action
):
    mock_stove.async_get_state.return_value = replace(
        stove_state, power_state=power, current_power=current
    )
    await setup_entry.runtime_data.async_refresh()
    state = hass.states.get("climate.pellet_stove_thermostat")
    assert state.state == mode
    assert state.attributes["hvac_action"] == action


async def test_power_command_error_becomes_home_assistant_error(
    hass, setup_entry, mock_stove
):
    mock_stove.async_set_power.side_effect = PelletStoveResponseError
    with pytest.raises(HomeAssistantError, match="Pellet stove command failed"):
        await hass.services.async_call(
            "climate",
            "turn_on",
            {"entity_id": "climate.pellet_stove_thermostat"},
            blocking=True,
        )


async def test_fractional_temperature_is_rejected(hass, setup_entry, mock_stove):
    with pytest.raises(ServiceValidationError) as error:
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": "climate.pellet_stove_thermostat", "temperature": 23.5},
            blocking=True,
        )
    assert error.value.translation_domain == "pstove"
    assert error.value.translation_key == "whole_temperature_required"
    assert "must be a whole number of degrees Celsius" in str(error.value)
    mock_stove.async_write.assert_not_awaited()
