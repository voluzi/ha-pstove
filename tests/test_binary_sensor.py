from dataclasses import replace
from importlib.util import find_spec

import pytest
from aiopelletstove import PowerState
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, UnitOfPower
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util

if find_spec("pytest_homeassistant"):
    from pytest_homeassistant.common import async_fire_time_changed_exact
else:
    from pytest_homeassistant_custom_component.common import (
        async_fire_time_changed_exact,
    )

PLUG = "sensor.stove_plug_power"
MISMATCH = "binary_sensor.pellet_stove_board_state_mismatch"


async def _setup(hass, entry, mock_stove, stove_state, reported, **options):
    mock_stove.async_get_state.return_value = replace(stove_state, power_state=reported)
    hass.config_entries.async_update_entry(
        entry, options={"power_sensor": PLUG, **options}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _power(hass, value, unit=UnitOfPower.WATT):
    hass.states.async_set(PLUG, value, {ATTR_UNIT_OF_MEASUREMENT: unit})
    await hass.async_block_till_done()


async def _advance(hass, freezer, seconds):
    freezer.tick(seconds)
    async_fire_time_changed_exact(hass, dt_util.utcnow())
    await hass.async_block_till_done()


def _issue(issue_registry, entry):
    return issue_registry.async_get_issue(
        "pstove", f"board_state_mismatch_{entry.entry_id}"
    )


@pytest.mark.parametrize(
    "reported,options,normal,mismatch,unit,seconds",
    [
        (PowerState.OFF, {}, "29.9", "30", UnitOfPower.WATT, 300),
        (
            PowerState.AWAITING_FLAME_1,
            {"power_threshold": 45},
            "44.9",
            "45",
            UnitOfPower.WATT,
            300,
        ),
        (PowerState.AWAITING_FLAME_2, {}, "29.9", "30", UnitOfPower.WATT, 300),
        (PowerState.RUNNING, {}, "10", "9.9", UnitOfPower.WATT, 600),
        (PowerState.FINAL_CLEANING, {}, "10", "9.9", UnitOfPower.WATT, 600),
        (PowerState.OFF, {}, "0.0299", "0.03", UnitOfPower.KILO_WATT, 300),
        (PowerState.RUNNING, {}, "0.01", "0.009", UnitOfPower.KILO_WATT, 600),
    ],
)
async def test_board_mismatch_requires_continuous_disagreement(
    hass,
    entry,
    mock_stove,
    stove_state,
    freezer,
    issue_registry,
    reported,
    options,
    normal,
    mismatch,
    unit,
    seconds,
):
    await _power(hass, normal, unit)
    await _setup(hass, entry, mock_stove, stove_state, reported, **options)
    await _advance(hass, freezer, seconds)
    assert hass.states.get(MISMATCH).state == "off"
    assert _issue(issue_registry, entry) is None

    await _power(hass, mismatch, unit)
    await _advance(hass, freezer, seconds - 1)
    assert hass.states.get(MISMATCH).state == "off"
    assert _issue(issue_registry, entry) is None
    await _advance(hass, freezer, 1)
    assert hass.states.get(MISMATCH).state == "on"
    assert _issue(issue_registry, entry).translation_key == "board_state_mismatch"

    await _power(hass, normal, unit)
    assert hass.states.get(MISMATCH).state == "off"
    assert _issue(issue_registry, entry) is None
    mock_stove.async_set_power.assert_not_awaited()
    mock_stove.async_write.assert_not_awaited()


@pytest.mark.parametrize(
    "invalid,unit",
    [
        ("unknown", UnitOfPower.WATT),
        ("unavailable", UnitOfPower.WATT),
        ("bad", UnitOfPower.WATT),
        ("nan", UnitOfPower.WATT),
        ("inf", UnitOfPower.WATT),
        ("60", "kWh"),
    ],
)
async def test_invalid_plug_reading_breaks_continuity(
    hass, entry, mock_stove, stove_state, freezer, issue_registry, invalid, unit
):
    await _power(hass, "60")
    await _setup(hass, entry, mock_stove, stove_state, PowerState.OFF)
    await _advance(hass, freezer, 299)
    await _power(hass, invalid, unit)
    await _advance(hass, freezer, 301)
    assert hass.states.get(MISMATCH).state == "unavailable"
    assert _issue(issue_registry, entry) is None

    await _power(hass, "60")
    await _advance(hass, freezer, 299)
    assert hass.states.get(MISMATCH).state == "off"
    await _advance(hass, freezer, 1)
    assert hass.states.get(MISMATCH).state == "on"

    await _power(hass, invalid, unit)
    assert hass.states.get(MISMATCH).state == "unavailable"
    assert _issue(issue_registry, entry) is None


async def test_board_recovery_clears_issue_without_a_plug_update(
    hass, entry, mock_stove, stove_state, freezer, issue_registry
):
    await _power(hass, "60")
    await _setup(hass, entry, mock_stove, stove_state, PowerState.OFF)
    await _advance(hass, freezer, 200)
    await _power(hass, "40")
    await _advance(hass, freezer, 100)
    assert hass.states.get(MISMATCH).state == "on"
    assert _issue(issue_registry, entry) is not None

    mock_stove.async_get_state.return_value = replace(
        stove_state, power_state=PowerState.RUNNING
    )
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(MISMATCH).state == "off"
    assert _issue(issue_registry, entry) is None


async def test_clearing_sensor_option_removes_active_diagnostic(
    hass, setup_entry, issue_registry, freezer, mock_stove, stove_state
):
    mock_stove.async_get_state.return_value = replace(
        stove_state, power_state=PowerState.OFF
    )
    await _power(hass, "50")
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"scan_interval": 10, "power_sensor": PLUG, "power_threshold": 40},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    await _advance(hass, freezer, 300)
    assert hass.states.get(MISMATCH).state == "on"
    assert _issue(issue_registry, setup_entry) is not None

    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval": 10, "power_threshold": 40}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get(MISMATCH).state == "unavailable"
    assert _issue(issue_registry, setup_entry) is None
    await _advance(hass, freezer, 300)
    assert _issue(issue_registry, setup_entry) is None
