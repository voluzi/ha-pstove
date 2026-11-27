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
from homeassistant.components import persistent_notification
from homeassistant.data_entry_flow import FlowResultType
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
CLEAR_BUTTON = "button.pellet_stove_clear_pending_power_command"
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

    mock_stove.async_read.return_value = int(PowerState.LIGHTING)
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
        return int(PowerState.OFF)

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


@pytest.mark.parametrize(
    "on,reported,mode",
    [
        (True, PowerState.AWAITING_FLAME_1, ("heat", "heating")),
        (True, PowerState.AWAITING_FLAME_2, ("heat", "heating")),
        (True, PowerState.STANDBY, ("heat", "idle")),
        (True, PowerState.ASHPAN_CLEANING, ("heat", "heating")),
        (False, PowerState.FINAL_CLEANING, ("off", "off")),
        (False, PowerState.ASHPAN_CLEANING, ("heat", "heating")),
    ],
)
async def test_intermediate_state_updates_display_without_releasing_guard(
    hass, entry, mock_stove, freezer, on, reported, mode
):
    hass.config_entries.async_update_entry(entry, options={"scan_interval": 300})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    mock_stove.async_set_power.return_value = (
        TOGGLED_FROM_OFF if on else TOGGLED_FROM_RUNNING
    )
    await _turn(hass, "turn_on" if on else "turn_off")
    mock_stove.async_read.return_value = int(reported)
    await _tick(hass, freezer)
    assert _mode(hass) == mode
    with pytest.raises(ServiceValidationError) as error:
        await _turn(hass, "turn_off" if on else "turn_on")
    assert error.value.translation_key == "power_change_pending"
    mock_stove.async_set_power.assert_awaited_once_with(on)
    assert not _notifications(hass)


@pytest.mark.parametrize("acknowledged", [True, False])
async def test_timeout_keeps_polling_and_blocks_second_toggle(
    hass, setup_entry, mock_stove, stove_state, freezer, acknowledged
):
    mock_stove.async_get_state.return_value = replace(
        stove_state, power_state=PowerState.OFF
    )
    await setup_entry.runtime_data.async_refresh()
    mock_stove.async_set_power.return_value = TOGGLED_FROM_OFF
    mock_stove.async_read.return_value = int(PowerState.OFF)
    if acknowledged:
        await _turn(hass, "turn_on")
        assert _mode(hass) == ("heat", "preheating")
    else:
        mock_stove.async_set_power.side_effect = PelletStovePowerOutcomeUnknownError
        with pytest.raises(HomeAssistantError) as error:
            await _turn(hass, "turn_on")
        assert error.value.translation_key == "power_unacknowledged"
        mock_stove.async_set_power.side_effect = None
        assert _mode(hass) == ("off", "off")

    await _tick(hass, freezer, POWER_CONFIRM_TIMEOUT + 1)
    assert _mode(hass) == ("off", "off")
    [notification] = _notifications(hass)

    await _turn(hass, "turn_on")
    mock_stove.async_set_power.assert_awaited_once_with(True)
    with pytest.raises(ServiceValidationError) as error:
        await _turn(hass, "turn_off")
    assert error.value.translation_key == "power_change_pending"

    persistent_notification.async_dismiss(hass, notification)
    mock_stove.async_read.reset_mock()
    await _tick(hass, freezer, 2 * 60 * 60)
    mock_stove.async_read.assert_awaited_with(POWER_STATE)
    assert not _notifications(hass)
    await _turn(hass, "turn_on")
    mock_stove.async_set_power.assert_awaited_once_with(True)


@pytest.mark.parametrize(
    "reported,result",
    [
        (PowerState.OFF, PowerResult.TOGGLED),
        (PowerState.ALARM, PowerResult.BLOCKED),
        (PowerState.ALARM_MEMORY, PowerResult.BLOCKED),
    ],
)
async def test_board_report_releases_timed_out_guard(
    hass, setup_entry, mock_stove, stove_state, freezer, reported, result
):
    before_toggle = PowerState.AWAITING_FLAME_1
    mock_stove.async_set_power.return_value = _outcome(
        PowerResult.TOGGLED, before_toggle
    )
    mock_stove.async_read.return_value = int(before_toggle)
    await _turn(hass, "turn_off")
    await _tick(hass, freezer, POWER_CONFIRM_TIMEOUT + 1)
    assert _notifications(hass)

    mock_stove.async_read.return_value = int(reported)
    mock_stove.async_get_state.return_value = replace(stove_state, power_state=reported)
    await _tick(hass, freezer)

    mock_stove.async_set_power.return_value = _outcome(result, reported)
    if result == PowerResult.BLOCKED:
        [notification] = _notifications(hass)
        assert (
            "reported an alarm"
            in hass.data["persistent_notification"][notification]["message"]
        )
        with pytest.raises(ServiceValidationError) as error:
            await _turn(hass, "turn_on")
        assert error.value.translation_key == "alarm"
    else:
        assert not _notifications(hass)
        await _turn(hass, "turn_on")
    assert mock_stove.async_set_power.await_count == 2


async def test_lost_ack_keeps_guard_until_requested_state(
    hass, setup_entry, mock_stove, freezer
):
    mock_stove.async_set_power.side_effect = PelletStovePowerOutcomeUnknownError
    with pytest.raises(HomeAssistantError) as error:
        await _turn(hass, "turn_off")
    assert error.value.translation_key == "power_unacknowledged"
    mock_stove.async_set_power.side_effect = None

    mock_stove.async_read.return_value = int(PowerState.ASHPAN_CLEANING)
    await _tick(hass, freezer, POWER_CONFIRM_TIMEOUT + 1)
    await _turn(hass, "turn_off")
    mock_stove.async_set_power.assert_awaited_once_with(False)

    mock_stove.async_read.return_value = int(PowerState.RUNNING)
    await _tick(hass, freezer)
    await _turn(hass, "turn_off")
    mock_stove.async_set_power.assert_awaited_once_with(False)

    mock_stove.async_read.return_value = int(PowerState.OFF)
    await _tick(hass, freezer)
    mock_stove.async_set_power.return_value = TOGGLED_FROM_RUNNING
    await _turn(hass, "turn_off")
    assert mock_stove.async_set_power.await_count == 2


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

    mock_stove.async_read.return_value = int(PowerState.OFF)
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


@pytest.mark.parametrize("timed_out", [False, True])
async def test_options_change_keeps_power_guard(
    hass, setup_entry, mock_stove, freezer, timed_out
):
    mock_stove.async_set_power.return_value = TOGGLED_FROM_RUNNING
    mock_stove.async_read.return_value = int(PowerState.RUNNING)
    await _turn(hass, "turn_off")
    if timed_out:
        await _tick(hass, freezer, POWER_CONFIRM_TIMEOUT + 1)
    notifications = _notifications(hass)
    assert bool(notifications) is timed_out

    flow = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"scan_interval": 30}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert setup_entry.runtime_data.update_interval == timedelta(seconds=30)
    assert _notifications(hass) == notifications
    with pytest.raises(ServiceValidationError) as error:
        await _turn(hass, "turn_on")
    assert error.value.translation_key == "power_change_pending"
    await _turn(hass, "turn_off")
    mock_stove.async_set_power.assert_awaited_once_with(False)


async def test_clear_button_resets_guard_without_contacting_stove(
    hass, setup_entry, mock_stove, freezer
):
    mock_stove.async_set_power.return_value = TOGGLED_FROM_RUNNING
    mock_stove.async_read.return_value = int(PowerState.RUNNING)
    await _turn(hass, "turn_off")
    await _tick(hass, freezer, POWER_CONFIRM_TIMEOUT + 1)
    assert _notifications(hass)

    coordinator = setup_entry.runtime_data
    mock_stove.async_get_state.side_effect = PelletStoveConnectionError
    await coordinator.async_refresh()
    assert hass.states.get(ENTITY_ID).state == "unavailable"
    mock_stove.async_get_state.reset_mock()
    mock_stove.async_read.reset_mock()
    await hass.services.async_call(
        "button", "press", {"entity_id": CLEAR_BUTTON}, blocking=True
    )
    assert not _notifications(hass)
    mock_stove.async_get_state.assert_not_awaited()
    mock_stove.async_set_power.assert_awaited_once_with(False)
    mock_stove.async_write.assert_not_awaited()
    await _tick(hass, freezer)
    mock_stove.async_read.assert_not_awaited()

    mock_stove.async_get_state.side_effect = None
    await coordinator.async_refresh()
    await _turn(hass, "turn_off")
    assert mock_stove.async_set_power.await_count == 2


async def test_removal_clears_power_guard(hass, setup_entry, mock_stove):
    mock_stove.async_set_power.return_value = TOGGLED_FROM_RUNNING
    await _turn(hass, "turn_off")
    await hass.config_entries.async_remove(setup_entry.entry_id)
    replacement = type(setup_entry)(
        domain=setup_entry.domain,
        title=setup_entry.title,
        entry_id=setup_entry.entry_id,
        unique_id=setup_entry.unique_id,
        minor_version=setup_entry.minor_version,
        data=setup_entry.data,
    )
    replacement.add_to_hass(hass)
    assert await hass.config_entries.async_setup(replacement.entry_id)
    await hass.async_block_till_done()
    await _turn(hass, "turn_off")
    assert mock_stove.async_set_power.await_count == 2


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

    try:
        mock_stove.async_read.side_effect = None
        mock_stove.async_read.return_value = int(PowerState.OFF)
        assert await hass.config_entries.async_reload(setup_entry.entry_id)
        await _turn(hass, "turn_on")
        mock_stove.async_set_power.assert_awaited_once_with(True)
    finally:
        release.set()
    await hass.async_block_till_done()
    # The old entity's alarm read must not clear the replacement's command.
    assert _mode(hass) == ("heat", "preheating")
    assert not _notifications(hass)
    with pytest.raises(ServiceValidationError) as error:
        await _turn(hass, "turn_off")
    assert error.value.translation_key == "power_change_pending"


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
