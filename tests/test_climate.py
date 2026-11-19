from dataclasses import replace
from datetime import timedelta
from importlib.util import find_spec

import pytest
from aiopelletstove import PelletStoveResponseError, PowerState
from aiopelletstove.registers import POWER_LEVEL, POWER_STATE, TARGET_TEMPERATURE
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.pstove.const import POWER_CONFIRM_TIMEOUT

if find_spec("pytest_homeassistant"):
    from pytest_homeassistant.common import async_fire_time_changed
else:
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

ENTITY_ID = "climate.pellet_stove_thermostat"
NOTIFICATION_PREFIX = "pstove_"


async def test_climate_controls(hass, setup_entry, mock_stove, stove_state):
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
    for service, current, value, reported in [
        ("turn_off", PowerState.RUNNING, False, PowerState.OFF),
        ("turn_on", PowerState.OFF, True, PowerState.RUNNING),
    ]:
        mock_stove.async_read.return_value = int(current)
        mock_stove.async_get_state.return_value = replace(
            stove_state, power_state=reported
        )
        await hass.services.async_call(
            "climate", service, {"entity_id": entity_id}, blocking=True
        )
        mock_stove.async_read.assert_awaited_with(POWER_STATE)
        mock_stove.async_set_power.assert_awaited_with(value)
        await setup_entry.runtime_data.async_refresh()
        await hass.async_block_till_done()


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
    mock_stove.async_read.return_value = int(PowerState.OFF)
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


def _notifications(hass):
    return [
        key
        for key in hass.data.get("persistent_notification", {})
        if key.startswith(NOTIFICATION_PREFIX)
    ]


async def _set_stove(hass, entry, mock_stove, stove_state, power):
    mock_stove.async_get_state.return_value = replace(stove_state, power_state=power)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_turn_on_shows_requested_mode_until_confirmed(
    hass, setup_entry, mock_stove, stove_state
):
    await _set_stove(hass, setup_entry, mock_stove, stove_state, PowerState.OFF)
    mock_stove.async_read.return_value = int(PowerState.OFF)
    await hass.services.async_call(
        "climate", "turn_on", {"entity_id": ENTITY_ID}, blocking=True
    )
    mock_stove.async_set_power.assert_awaited_once_with(True)
    state = hass.states.get(ENTITY_ID)
    assert state.state == "heat"
    assert state.attributes["hvac_action"] == "preheating"

    await _set_stove(hass, setup_entry, mock_stove, stove_state, PowerState.OFF)
    assert hass.states.get(ENTITY_ID).attributes["hvac_action"] == "preheating"

    await _set_stove(
        hass, setup_entry, mock_stove, stove_state, PowerState.AWAITING_FLAME_1
    )
    state = hass.states.get(ENTITY_ID)
    assert state.state == "heat"
    assert state.attributes["hvac_action"] == "heating"

    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=POWER_CONFIRM_TIMEOUT + 1)
    )
    await hass.async_block_till_done()
    assert not _notifications(hass)


async def test_unconfirmed_power_on_reverts_and_notifies(
    hass, setup_entry, mock_stove, stove_state
):
    await _set_stove(hass, setup_entry, mock_stove, stove_state, PowerState.OFF)
    mock_stove.async_read.return_value = int(PowerState.OFF)
    await hass.services.async_call(
        "climate", "turn_on", {"entity_id": ENTITY_ID}, blocking=True
    )
    assert hass.states.get(ENTITY_ID).state == "heat"

    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=POWER_CONFIRM_TIMEOUT + 1)
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state.state == "off"
    assert state.attributes["hvac_action"] == "off"
    assert len(_notifications(hass)) == 1

    await _set_stove(hass, setup_entry, mock_stove, stove_state, PowerState.RUNNING)
    mock_stove.async_read.return_value = int(PowerState.RUNNING)
    await hass.services.async_call(
        "climate", "turn_off", {"entity_id": ENTITY_ID}, blocking=True
    )
    assert not _notifications(hass)


async def test_turn_off_shows_off_until_shutdown_starts(
    hass, setup_entry, mock_stove, stove_state
):
    mock_stove.async_read.return_value = int(PowerState.RUNNING)
    await hass.services.async_call(
        "climate", "turn_off", {"entity_id": ENTITY_ID}, blocking=True
    )
    state = hass.states.get(ENTITY_ID)
    assert state.state == "off"
    assert state.attributes["hvac_action"] == "off"

    await _set_stove(
        hass, setup_entry, mock_stove, stove_state, PowerState.FINAL_CLEANING
    )
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=POWER_CONFIRM_TIMEOUT + 1)
    )
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == "off"
    assert not _notifications(hass)


async def test_turn_on_skips_toggle_when_already_on(hass, setup_entry, mock_stove):
    mock_stove.async_read.return_value = int(PowerState.LIGHTING)
    await hass.services.async_call(
        "climate", "turn_on", {"entity_id": ENTITY_ID}, blocking=True
    )
    mock_stove.async_set_power.assert_not_awaited()


async def test_turn_on_rejected_during_final_cleaning(hass, setup_entry, mock_stove):
    mock_stove.async_read.return_value = int(PowerState.FINAL_CLEANING)
    with pytest.raises(ServiceValidationError) as error:
        await hass.services.async_call(
            "climate", "turn_on", {"entity_id": ENTITY_ID}, blocking=True
        )
    assert error.value.translation_key == "final_cleaning"
    mock_stove.async_set_power.assert_not_awaited()


async def test_turn_on_rejected_for_unknown_power_state(hass, setup_entry, mock_stove):
    mock_stove.async_read.return_value = 42
    with pytest.raises(HomeAssistantError) as error:
        await hass.services.async_call(
            "climate", "turn_on", {"entity_id": ENTITY_ID}, blocking=True
        )
    assert error.value.translation_key == "unknown_power_state"
    mock_stove.async_set_power.assert_not_awaited()


async def test_power_commands_wait_for_pending_confirmation(
    hass, setup_entry, mock_stove, stove_state
):
    await _set_stove(hass, setup_entry, mock_stove, stove_state, PowerState.OFF)
    mock_stove.async_read.return_value = int(PowerState.OFF)
    await hass.services.async_call(
        "climate", "turn_on", {"entity_id": ENTITY_ID}, blocking=True
    )
    await hass.services.async_call(
        "climate", "turn_on", {"entity_id": ENTITY_ID}, blocking=True
    )
    mock_stove.async_set_power.assert_awaited_once_with(True)

    with pytest.raises(ServiceValidationError) as error:
        await hass.services.async_call(
            "climate", "turn_off", {"entity_id": ENTITY_ID}, blocking=True
        )
    assert error.value.translation_key == "power_change_pending"
    mock_stove.async_set_power.assert_awaited_once_with(True)
