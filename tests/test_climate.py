import asyncio
from dataclasses import replace
from datetime import timedelta
from importlib.util import find_spec

import pytest
from aiopelletstove import (
    PelletStoveConnectionError,
    PelletStovePowerOutcomeUnknownError,
    PelletStoveRejectedError,
    PelletStoveResponseError,
    PowerCommandOutcome,
    PowerResult,
    PowerState,
)
from aiopelletstove.registers import POWER_LEVEL, POWER_STATE, TARGET_TEMPERATURE
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.pstove.const import (
    POWER_CONFIRM_POLL_INTERVAL,
    POWER_CONFIRM_TIMEOUT,
)

if find_spec("pytest_homeassistant"):
    from pytest_homeassistant.common import async_fire_time_changed
else:
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

ENTITY_ID = "climate.pellet_stove_thermostat"
NOTIFICATION_PREFIX = "pstove_"


def _outcome(result, state):
    return PowerCommandOutcome(result, state)


TOGGLED_FROM_OFF = _outcome(PowerResult.TOGGLED, PowerState.OFF)
TOGGLED_FROM_RUNNING = _outcome(PowerResult.TOGGLED, PowerState.RUNNING)


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
    for service, on, outcome, reported in [
        ("turn_off", False, TOGGLED_FROM_RUNNING, PowerState.OFF),
        ("turn_on", True, TOGGLED_FROM_OFF, PowerState.RUNNING),
    ]:
        mock_stove.async_set_power.return_value = outcome
        await hass.services.async_call(
            "climate", service, {"entity_id": entity_id}, blocking=True
        )
        mock_stove.async_set_power.assert_awaited_with(on)
        mock_stove.async_read.return_value = int(reported)
        mock_stove.async_get_state.return_value = replace(
            stove_state, power_state=reported
        )
        await _poll(hass)
        mock_stove.async_read.assert_awaited_with(POWER_STATE)
        assert hass.states.get(entity_id).state == ("heat" if on else "off")


@pytest.mark.parametrize(
    "power,current,mode,action",
    [
        (PowerState.RUNNING, 0, "heat", "idle"),
        (PowerState.LIGHTING, 0, "heat", "heating"),
        (PowerState.ASHPAN_CLEANING, 1, "heat", "heating"),
        (PowerState.FINAL_CLEANING, 0, "off", "off"),
        (PowerState.STANDBY, 0, "heat", "idle"),
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


def _notifications(hass):
    return [
        key
        for key in hass.data.get("persistent_notification", {})
        if key.startswith(NOTIFICATION_PREFIX)
    ]


async def _poll(hass, seconds=POWER_CONFIRM_POLL_INTERVAL + 1):
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


async def _turn(hass, service):
    await hass.services.async_call(
        "climate", service, {"entity_id": ENTITY_ID}, blocking=True
    )


async def _tick(hass, freezer, seconds=POWER_CONFIRM_POLL_INTERVAL + 1):
    freezer.tick(seconds)
    await _poll(hass, 0)


def _mode(hass):
    state = hass.states.get(ENTITY_ID)
    return state.state, state.attributes["hvac_action"]


async def test_turn_on_shows_requested_mode_until_confirmed(
    hass, setup_entry, mock_stove, freezer
):
    mock_stove.async_set_power.return_value = TOGGLED_FROM_OFF
    mock_stove.async_read.return_value = int(PowerState.OFF)
    await _turn(hass, "turn_on")
    assert _mode(hass) == ("heat", "preheating")

    await _tick(hass, freezer)
    assert _mode(hass) == ("heat", "preheating")

    mock_stove.async_read.return_value = int(PowerState.AWAITING_FLAME_1)
    await _tick(hass, freezer)
    assert _mode(hass) == ("heat", "heating")
    assert not _notifications(hass)


async def test_confirmation_outranks_an_older_full_poll(
    hass, setup_entry, mock_stove, stove_state, freezer
):
    mock_stove.async_set_power.return_value = TOGGLED_FROM_RUNNING
    await _turn(hass, "turn_off")

    # A full poll samples RUNNING, then finishes after the confirming read.
    release = asyncio.Event()
    started = asyncio.Event()
    read_finished = asyncio.Event()
    polled = replace(stove_state, air_temperature=22)

    async def slow_state(**_kwargs):
        started.set()
        await release.wait()
        return polled

    async def read_power(_register):
        read_finished.set()
        return int(PowerState.FINAL_CLEANING)

    mock_stove.async_get_state.side_effect = slow_state
    old_poll = hass.async_create_task(setup_entry.runtime_data.async_request_refresh())
    await started.wait()

    try:
        mock_stove.async_read.side_effect = read_power
        freezer.tick(POWER_CONFIRM_POLL_INTERVAL + 1)
        async_fire_time_changed(hass, dt_util.utcnow())
        await read_finished.wait()
        assert _mode(hass) == ("off", "off")
    finally:
        release.set()
        await old_poll
    await hass.async_block_till_done()
    assert _mode(hass) == ("off", "off")

    # Even an identical full sample takes over if it was read more recently.
    mock_stove.async_get_state.side_effect = None
    mock_stove.async_get_state.return_value = polled
    await setup_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert _mode(hass) == ("heat", "heating")


async def test_start_into_standby_shows_armed(
    hass, setup_entry, mock_stove, stove_state, freezer
):
    mock_stove.async_set_power.return_value = TOGGLED_FROM_OFF
    await _turn(hass, "turn_on")
    mock_stove.async_read.return_value = int(PowerState.STANDBY)
    mock_stove.async_get_state.return_value = replace(
        stove_state, power_state=PowerState.STANDBY
    )
    await _tick(hass, freezer)
    assert _mode(hass) == ("heat", "idle")
    assert not _notifications(hass)


async def test_unconfirmed_power_on_reverts_and_notifies(
    hass, setup_entry, mock_stove, stove_state, freezer
):
    mock_stove.async_get_state.return_value = replace(
        stove_state, power_state=PowerState.OFF
    )
    await setup_entry.runtime_data.async_refresh()
    mock_stove.async_set_power.return_value = TOGGLED_FROM_OFF
    mock_stove.async_read.return_value = int(PowerState.OFF)
    await _turn(hass, "turn_on")
    assert _mode(hass) == ("heat", "preheating")

    await _tick(hass, freezer, POWER_CONFIRM_TIMEOUT + 1)
    assert _mode(hass) == ("off", "off")
    assert len(_notifications(hass)) == 1

    await _turn(hass, "turn_on")
    assert not _notifications(hass)


async def test_alarm_ends_pending_power_on(
    hass, setup_entry, mock_stove, stove_state, freezer
):
    mock_stove.async_set_power.return_value = TOGGLED_FROM_OFF
    await _turn(hass, "turn_on")
    mock_stove.async_read.return_value = int(PowerState.ALARM)
    mock_stove.async_get_state.return_value = replace(
        stove_state, power_state=PowerState.ALARM
    )
    await _tick(hass, freezer)
    assert _mode(hass) == ("off", "off")
    assert len(_notifications(hass)) == 1


async def test_failed_and_unknown_reads_are_not_confirmation(
    hass, setup_entry, mock_stove, freezer
):
    mock_stove.async_set_power.return_value = TOGGLED_FROM_RUNNING
    await _turn(hass, "turn_off")
    mock_stove.async_read.side_effect = PelletStoveResponseError
    await _tick(hass, freezer)
    assert _mode(hass) == ("off", "off")

    mock_stove.async_read.side_effect = None
    mock_stove.async_read.return_value = 42
    await _tick(hass, freezer)
    with pytest.raises(ServiceValidationError) as error:
        await _turn(hass, "turn_on")
    assert error.value.translation_key == "power_change_pending"

    mock_stove.async_read.return_value = int(PowerState.FINAL_CLEANING)
    await _tick(hass, freezer)
    assert not _notifications(hass)
    mock_stove.async_set_power.assert_awaited_once()


async def test_lost_acknowledgement_still_blocks_a_second_toggle(
    hass, setup_entry, mock_stove, stove_state, freezer
):
    mock_stove.async_get_state.return_value = replace(
        stove_state, power_state=PowerState.OFF
    )
    await setup_entry.runtime_data.async_refresh()
    mock_stove.async_set_power.side_effect = PelletStovePowerOutcomeUnknownError
    mock_stove.async_read.return_value = int(PowerState.OFF)
    with pytest.raises(HomeAssistantError) as error:
        await _turn(hass, "turn_on")
    assert error.value.translation_key == "power_unacknowledged"
    # No optimism without an acknowledgement, but no second toggle either.
    assert _mode(hass) == ("off", "off")
    await _turn(hass, "turn_on")
    mock_stove.async_set_power.assert_awaited_once()

    mock_stove.async_read.return_value = int(PowerState.LIGHTING)
    mock_stove.async_get_state.return_value = replace(
        stove_state, power_state=PowerState.LIGHTING
    )
    await _tick(hass, freezer)
    assert _mode(hass) == ("heat", "heating")
    assert not _notifications(hass)


@pytest.mark.parametrize(
    "failure,key",
    [(PelletStoveConnectionError, None), (PelletStoveRejectedError, "power_rejected")],
)
async def test_known_power_failure_leaves_no_guard(
    hass, setup_entry, mock_stove, failure, key
):
    mock_stove.async_set_power.side_effect = failure
    with pytest.raises(HomeAssistantError) as error:
        await _turn(hass, "turn_off")
    assert error.value.translation_key == key
    mock_stove.async_set_power.side_effect = None
    mock_stove.async_set_power.return_value = TOGGLED_FROM_RUNNING
    await _turn(hass, "turn_off")
    assert mock_stove.async_set_power.await_count == 2


async def test_confirmation_window_starts_at_acknowledgement(
    hass, setup_entry, mock_stove, freezer
):
    async def slow_set_power(_on):
        freezer.tick(POWER_CONFIRM_TIMEOUT)
        return TOGGLED_FROM_OFF

    mock_stove.async_set_power.side_effect = slow_set_power
    mock_stove.async_read.return_value = int(PowerState.OFF)
    await _turn(hass, "turn_on")
    await _tick(hass, freezer)
    assert _mode(hass) == ("heat", "preheating")
    assert not _notifications(hass)


async def test_overlapping_commands_toggle_once(hass, setup_entry, mock_stove):
    release = asyncio.Event()

    async def slow_set_power(_on):
        await release.wait()
        return TOGGLED_FROM_OFF

    mock_stove.async_set_power.side_effect = slow_set_power
    first = hass.async_create_task(_turn(hass, "turn_on"))
    second = hass.async_create_task(_turn(hass, "turn_on"))
    third = hass.async_create_task(_turn(hass, "turn_off"))
    await asyncio.sleep(0)
    release.set()
    await first
    await second
    with pytest.raises(ServiceValidationError) as error:
        await third
    assert error.value.translation_key == "power_change_pending"
    mock_stove.async_set_power.assert_awaited_once()


async def test_pending_command_survives_reload(hass, setup_entry, mock_stove, freezer):
    mock_stove.async_set_power.return_value = TOGGLED_FROM_OFF
    mock_stove.async_read.return_value = int(PowerState.OFF)
    await _turn(hass, "turn_on")
    assert await hass.config_entries.async_reload(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert _mode(hass) == ("heat", "preheating")
    await _turn(hass, "turn_on")
    mock_stove.async_set_power.assert_awaited_once()

    mock_stove.async_read.return_value = int(PowerState.LIGHTING)
    await _tick(hass, freezer)
    assert _mode(hass) == ("heat", "heating")


async def test_detached_poll_leaves_replacement_pending(
    hass, setup_entry, mock_stove, freezer
):
    mock_stove.async_set_power.return_value = TOGGLED_FROM_OFF
    await _turn(hass, "turn_on")
    release = asyncio.Event()
    started = asyncio.Event()

    async def blocked_read(_register):
        started.set()
        await release.wait()
        return int(PowerState.ALARM)

    mock_stove.async_read.side_effect = blocked_read
    freezer.tick(POWER_CONFIRM_POLL_INTERVAL + 1)
    async_fire_time_changed(hass, dt_util.utcnow())
    await started.wait()

    mock_stove.async_read.side_effect = None
    mock_stove.async_read.return_value = int(PowerState.OFF)
    assert await hass.config_entries.async_reload(setup_entry.entry_id)
    await hass.async_block_till_done()
    release.set()
    await hass.async_block_till_done()
    # The old entity's alarm read must not clear the replacement's command.
    assert _mode(hass) == ("heat", "preheating")
    assert not _notifications(hass)


@pytest.mark.parametrize(
    "state,key",
    [
        (PowerState.FINAL_CLEANING, "final_cleaning"),
        (PowerState.STANDBY, "standby"),
        (PowerState.ALARM_MEMORY, "alarm"),
        (PowerState(42), "unknown_power_state"),
    ],
)
async def test_blocked_power_command_is_explained(
    hass, setup_entry, mock_stove, state, key
):
    mock_stove.async_set_power.return_value = _outcome(PowerResult.BLOCKED, state)
    with pytest.raises(HomeAssistantError) as error:
        await _turn(hass, "turn_on")
    assert error.value.translation_key == key
    # A blocked command leaves no pending guard, so a later retry is sent.
    with pytest.raises(HomeAssistantError):
        await _turn(hass, "turn_on")
    assert mock_stove.async_set_power.await_count == 2


async def test_unchanged_power_sets_no_pending(hass, setup_entry, mock_stove):
    mock_stove.async_set_power.return_value = _outcome(
        PowerResult.UNCHANGED, PowerState.LIGHTING
    )
    await _turn(hass, "turn_on")
    await _turn(hass, "turn_off")
    assert mock_stove.async_set_power.await_count == 2
