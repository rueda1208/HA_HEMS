import json

from controller.utils import utils


def test_device_configuration_can_be_loaded_from_mock_json(tmp_path, monkeypatch):
    configuration = {
        "hub.hems201": {"device_type": "hub", "mode": {"value": "heating"}},
        "climate.sous_sol_2": {"device_type": "thermostat", "setpoint": {"value": 21.0}},
    }
    configuration_path = tmp_path / "devices.json"
    configuration_path.write_text(json.dumps(configuration), encoding="utf-8")
    monkeypatch.setenv("HEMS_DATA_SOURCE", "mock")
    monkeypatch.setenv("MOCK_CONFIGURATION_PATH", str(configuration_path))

    assert utils.retrieve_device_configuration() == configuration


def test_empty_mock_gdp_file_avoids_hems_api_request(tmp_path, monkeypatch):
    events_path = tmp_path / "peak-events.json"
    events_path.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("HEMS_DATA_SOURCE", "mock")
    monkeypatch.setenv("MOCK_GDP_EVENTS_PATH", str(events_path))

    assert utils.retrieve_gdp_event() is None


def test_mock_source_fails_clearly_when_a_required_file_is_missing(monkeypatch):
    monkeypatch.setenv("HEMS_DATA_SOURCE", "mock")
    monkeypatch.delenv("MOCK_CONFIGURATION_PATH", raising=False)

    try:
        utils.retrieve_device_configuration()
    except ValueError as error:
        assert "MOCK_CONFIGURATION_PATH is required" in str(error)
    else:
        raise AssertionError("Expected missing mock configuration path to fail")