from dataclasses import replace
from unittest.mock import patch

import pytest
from aiohttp import ClientConnectionError
from aiopelletstove import PelletStoveConnectionError
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er


async def test_unavailable_and_recovery(hass, setup_entry, mock_stove):
    coordinator = setup_entry.runtime_data
    mock_stove.async_get_state.side_effect = PelletStoveConnectionError
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("climate.pellet_stove_thermostat").state == "unavailable"
    mock_stove.async_get_state.side_effect = None
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("climate.pellet_stove_thermostat").state == "heat"


async def test_removing_entry_dismisses_only_its_power_notification(hass, setup_entry):
    own = f"pstove_{setup_entry.entry_id}_power"
    other = "pstove_other_entry_power"
    for notification in (own, other):
        persistent_notification.async_create(
            hass, "Power command not confirmed", notification_id=notification
        )

    await hass.config_entries.async_remove(setup_entry.entry_id)
    await hass.async_block_till_done()
    notifications = hass.data["persistent_notification"]
    assert own not in notifications
    assert other in notifications


async def test_initial_failure_retries_setup(hass, entry, mock_stove):
    mock_stove.async_get_state.side_effect = PelletStoveConnectionError
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_diagnostics_cadence_keeps_previous_values(
    hass, entry, mock_stove, stove_state
):
    with patch(
        "custom_components.pstove.coordinator.monotonic", return_value=1000
    ) as clock:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data
        mock_stove.async_get_state.assert_awaited_once_with(include_diagnostics=True)
        mock_stove.async_get_state.return_value = replace(
            stove_state,
            air_temperature=22.0,
            last_alarm=None,
            total_hours=None,
            partial_hours=None,
            ignition_count=None,
        )
        clock.return_value = 1299
        await coordinator.async_refresh()
        mock_stove.async_get_state.assert_awaited_with(include_diagnostics=False)
        assert coordinator.data.air_temperature == 22.0
        assert coordinator.data.total_hours == 4660
        assert hass.states.get("sensor.pellet_stove_total_hours").state == "4660"
        clock.return_value = 1300
        mock_stove.async_get_state.return_value = replace(stove_state, total_hours=4661)
        await coordinator.async_refresh()
        mock_stove.async_get_state.assert_awaited_with(include_diagnostics=True)
        assert coordinator.data.total_hours == 4661


async def test_registry_and_unload_reload(hass, setup_entry):
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, setup_entry.entry_id)
    contract = sorted(
        (e.entity_id, e.unique_id.removeprefix("stove.example_")) for e in entities
    )
    assert contract == [
        (
            "button.pellet_stove_clear_pending_power_command",
            "clear_pending_power_command",
        ),
        ("climate.pellet_stove_thermostat", "thermostat"),
        ("select.pellet_stove_fan_2_regulation", "fan2_regulation"),
        ("select.pellet_stove_fan_3_regulation", "fan3_regulation"),
        ("sensor.pellet_stove_air_temperature", "air_temperature"),
        ("sensor.pellet_stove_current_power", "current_power"),
        ("sensor.pellet_stove_fan_speed", "fan_speed"),
        ("sensor.pellet_stove_flue_temperature", "flue_temperature"),
        ("sensor.pellet_stove_ignition_count", "ignition_count"),
        ("sensor.pellet_stove_last_alarm", "last_alarm"),
        ("sensor.pellet_stove_partial_hours", "partial_hours"),
        ("sensor.pellet_stove_power_state", "power_state"),
        ("sensor.pellet_stove_total_hours", "total_hours"),
        ("switch.pellet_stove_buzzer", "buzzer"),
        ("switch.pellet_stove_fan", "fan"),
        ("switch.pellet_stove_standby", "standby"),
    ]
    assert len({e.device_id for e in entities}) == 1
    assert entities[0].device_id is not None
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    assert setup_entry.state is ConfigEntryState.NOT_LOADED
    assert await hass.config_entries.async_setup(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("climate.pellet_stove_thermostat").state == "heat"


async def test_failed_diagnostics_attempt_is_throttled(
    hass, entry, mock_stove, stove_state
):
    with patch(
        "custom_components.pstove.coordinator.monotonic", return_value=1000
    ) as clock:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data
        assert coordinator.update_interval.total_seconds() == 10
        clock.return_value = 1300
        mock_stove.async_get_state.side_effect = PelletStoveConnectionError
        await coordinator.async_refresh()
        mock_stove.async_get_state.assert_awaited_with(include_diagnostics=True)
        assert not coordinator.last_update_success
        mock_stove.async_get_state.side_effect = None
        mock_stove.async_get_state.return_value = replace(stove_state, total_hours=None)
        clock.return_value = 1310
        await coordinator.async_refresh()
        mock_stove.async_get_state.assert_awaited_with(include_diagnostics=False)
        assert coordinator.last_update_success
        assert coordinator.data.total_hours == 4660


async def test_mac_upgrade_preserves_existing_device_and_entities(
    hass, aioclient_mock, setup_entry
):
    registry = er.async_get(hass)
    before = {
        entity.entity_id: entity.device_id
        for entity in er.async_entries_for_config_entry(registry, setup_entry.entry_id)
    }
    assert before
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    hass.config_entries.async_update_entry(setup_entry, minor_version=1)
    aioclient_mock.get("http://stove.example/api/id", json={"mac": "020000000001"})

    assert await hass.config_entries.async_setup(setup_entry.entry_id)
    await hass.async_block_till_done()

    assert setup_entry.minor_version == 2
    assert setup_entry.unique_id == "02:00:00:00:00:01"
    after = {
        entity.entity_id: entity.device_id
        for entity in er.async_entries_for_config_entry(registry, setup_entry.entry_id)
    }
    assert after == before
    assert hass.states.get("climate.pellet_stove_thermostat").state == "heat"


@pytest.mark.parametrize("error", [ClientConnectionError, TimeoutError])
async def test_mac_upgrade_retries_after_connection_failure(
    hass, aioclient_mock, entry, mock_stove, error
):
    hass.config_entries.async_update_entry(entry, minor_version=1)
    aioclient_mock.get("http://stove.example/api/id", exc=error())

    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.minor_version == 1
    assert entry.unique_id == "stove.example"

    assert await hass.config_entries.async_unload(entry.entry_id)
    aioclient_mock.clear_requests()
    aioclient_mock.get("http://stove.example/api/id", json={"mac": "020000000001"})

    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.minor_version == 2
    assert entry.unique_id == "02:00:00:00:00:01"


async def test_mac_upgrade_finishes_when_identity_endpoint_returns_404(
    hass, aioclient_mock, entry, mock_stove
):
    hass.config_entries.async_update_entry(entry, minor_version=1)
    aioclient_mock.get("http://stove.example/api/id", status=404)

    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.minor_version == 2
    assert entry.unique_id == "stove.example"
