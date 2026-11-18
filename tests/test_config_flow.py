from datetime import timedelta
from unittest.mock import patch

import pytest
from aiopelletstove import (
    PelletStove,
    PelletStoveConnectionError,
    PelletStoveResponseError,
)
from homeassistant import config_entries, data_entry_flow


async def test_user_flow_normalizes_host(hass, mock_stove, aioclient_mock):
    aioclient_mock.get("http://stove.example/api/id", status=404)
    with patch(
        "homeassistant.config_entries.ConfigEntries.async_setup", return_value=True
    ):
        result = await hass.config_entries.flow.async_init(
            "pstove",
            context={"source": config_entries.SOURCE_USER},
            data={"host": " STOVE.EXAMPLE ", "name": "Study stove"},
        )
        await hass.async_block_till_done()
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "Study stove"
    assert result["data"] == {"host": "stove.example", "name": "Study stove"}
    assert result["result"].unique_id == "stove.example"
    mock_stove.async_get_state.assert_awaited_once_with(include_diagnostics=False)


async def test_same_mac_at_another_host_updates_existing_entry(
    hass, mock_stove, aioclient_mock, reload_entry
):
    aioclient_mock.get("http://stove.example/api/id", json={"mac": "020000000001"})
    aioclient_mock.get(
        "http://new-stove.example/api/id", json={"mac": "02:00:00:00:00:01"}
    )
    first = await hass.config_entries.flow.async_init(
        "pstove",
        context={"source": config_entries.SOURCE_USER},
        data={"host": "stove.example"},
    )
    await hass.async_block_till_done()

    entry = first["result"]
    assert entry.unique_id == "02:00:00:00:00:01"
    second = await hass.config_entries.flow.async_init(
        "pstove",
        context={"source": config_entries.SOURCE_USER},
        data={"host": "new-stove.example"},
    )
    await hass.async_block_till_done()
    assert second["reason"] == "already_configured"
    assert entry.data["host"] == "new-stove.example"
    reload_entry.assert_awaited_once_with(entry.entry_id)


@pytest.mark.parametrize("matches", [True, False])
async def test_reconfigure_requires_the_same_stove(
    hass, entry, mock_stove, aioclient_mock, reload_entry, matches
):
    hass.config_entries.async_update_entry(entry, unique_id="02:00:00:00:00:01")
    aioclient_mock.get(
        "http://new-stove.example/api/id",
        json={"mac": "020000000001" if matches else "020000000002"},
    )
    result = await hass.config_entries.flow.async_init(
        "pstove",
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "new-stove.example"}
    )
    await hass.async_block_till_done()

    assert entry.unique_id == "02:00:00:00:00:01"
    if matches:
        assert result["reason"] == "reconfigure_successful"
        assert entry.data["host"] == "new-stove.example"
    else:
        assert result["errors"] == {"base": "unique_id_mismatch"}
        assert entry.data["host"] == "stove.example"


@pytest.mark.parametrize(
    "error,expected",
    [
        (PelletStoveConnectionError, "cannot_connect"),
        (PelletStoveResponseError, "unknown"),
    ],
)
async def test_validation_errors(hass, mock_stove, error, expected):
    mock_stove.async_get_state.side_effect = error
    result = await hass.config_entries.flow.async_init(
        "pstove",
        context={"source": config_entries.SOURCE_USER},
        data={"host": "stove.example"},
    )
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_duplicate_host_aborts_before_contact(hass, entry, mock_stove):
    result = await hass.config_entries.flow.async_init(
        "pstove",
        context={"source": config_entries.SOURCE_USER},
        data={"host": "STOVE.EXAMPLE"},
    )
    assert result["reason"] == "already_configured"
    mock_stove.async_get_state.assert_not_awaited()


@pytest.mark.parametrize("interval", [10, 60, 300])
async def test_options_reload(hass, setup_entry, reload_entry, interval):
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"scan_interval": interval},
    )
    await hass.async_block_till_done()
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert setup_entry.options == {"scan_interval": interval}
    reload_entry.assert_awaited_once_with(setup_entry.entry_id)


@pytest.mark.parametrize("interval", [9, 301])
async def test_options_reject_out_of_range(hass, setup_entry, interval):
    from custom_components.pstove.config_flow import vol

    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    with pytest.raises(vol.Invalid):
        result["data_schema"]({"scan_interval": interval})


@pytest.mark.parametrize("host", ["http://stove.example", "stove.example/path"])
async def test_invalid_host_has_field_error_without_unique_id(hass, mock_stove, host):
    with patch(
        "custom_components.pstove.config_flow.PelletStove", side_effect=PelletStove
    ):
        result = await hass.config_entries.flow.async_init(
            "pstove",
            context={"source": config_entries.SOURCE_USER},
            data={"host": host},
        )
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"host": "invalid_host"}
    assert (
        hass.config_entries.flow.async_get(result["flow_id"])["context"].get(
            "unique_id"
        )
        is None
    )
    mock_stove.async_get_state.assert_not_awaited()


async def test_stored_poll_interval_is_used(hass, entry, mock_stove):
    hass.config_entries.async_update_entry(entry, options={"scan_interval": 60})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.update_interval == timedelta(seconds=60)
