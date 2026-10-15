import json
from unittest.mock import MagicMock, patch

from controller.devices.heat_pump import calculate_heat_pump_cop, get_heat_pump_cop
from controller.utils.configuration import _get_heat_pump_specifications
from controller.utils.utils import ControlMode


def test_heat_pump_specifications_are_fetched_once_for_repeated_evaluations(monkeypatch):
    monkeypatch.setenv("HEMS_API_BASE_URL", "http://hems.example.test")
    monkeypatch.setenv("HEAT_PUMP_MODEL", "test-model")
    _get_heat_pump_specifications.cache_clear()

    response = MagicMock()
    response.json.return_value = {
        "heating": {
            "COP_points": {
                "cold": {"outdoor_dry_bulb_C": -10.0, "max": 2.0},
                "mild": {"outdoor_dry_bulb_C": 0.0, "max": 3.0},
                "warm": {"outdoor_dry_bulb_C": 10.0, "max": 4.0},
            }
        }
    }

    with patch("controller.utils.configuration.requests.get", return_value=response) as get:
        cold_cop = get_heat_pump_cop(ControlMode.HEATING, -5.0)
        warm_cop = get_heat_pump_cop(ControlMode.HEATING, 5.0)

    assert cold_cop != warm_cop
    get.assert_called_once_with(
        "http://hems.example.test/api/devices/specifications/test-model", verify=False, timeout=10
    )
    monkeypatch.setenv("HEMS_DATA_SOURCE", "api")
    _get_heat_pump_specifications.cache_clear()


def test_heat_pump_cop_uses_mock_specification_file_without_http(tmp_path, monkeypatch):
    specification_path = tmp_path / "heat-pump-specifications.json"
    specification_path.write_text(
        json.dumps(
            {
                "heating": {
                    "COP_points": {
                        "cold": {"outdoor_dry_bulb_C": -10.0, "max": 2.0},
                        "mild": {"outdoor_dry_bulb_C": 0.0, "max": 3.0},
                        "warm": {"outdoor_dry_bulb_C": 10.0, "max": 4.0},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HEMS_DATA_SOURCE", "mock")
    monkeypatch.setenv("MOCK_HEAT_PUMP_SPECIFICATIONS_PATH", str(specification_path))

    with patch("controller.utils.configuration.requests.get") as get:
        result = get_heat_pump_cop(ControlMode.HEATING, 0.0)

    assert abs(result - 3.0) < 1e-9
    get.assert_not_called()


def test_heat_pump_cop_loads_repository_yaml_example(monkeypatch):
    from pathlib import Path

    specification_path = (
        Path(__file__).resolve().parents[3] / "config" / "controller" / "heat-pump.example.yaml"
    )
    monkeypatch.setenv("HEMS_DATA_SOURCE", "mock")
    monkeypatch.setenv("MOCK_HEAT_PUMP_SPECIFICATIONS_PATH", str(specification_path))

    with patch("controller.utils.configuration.requests.get") as get:
        result = get_heat_pump_cop(ControlMode.HEATING, 0.0)

    assert result > 0
    get.assert_not_called()


def test_calculate_heat_pump_cop_uses_requested_mode():
    specifications = {
        "heating": {
            "COP_points": {
                "cold": {"outdoor_dry_bulb_C": -10.0, "max": 2.0},
                "mild": {"outdoor_dry_bulb_C": 0.0, "max": 3.0},
                "warm": {"outdoor_dry_bulb_C": 10.0, "max": 4.0},
            }
        },
        "cooling": {
            "COP_points": {
                "mild": {"outdoor_dry_bulb_C": 15.0, "max": 3.0},
                "warm": {"outdoor_dry_bulb_C": 25.0, "max": 4.0},
                "hot": {"outdoor_dry_bulb_C": 35.0, "max": 5.0},
            }
        },
    }

    heating_cop = calculate_heat_pump_cop(specifications, ControlMode.HEATING, 0.0)
    cooling_cop = calculate_heat_pump_cop(specifications, ControlMode.COOLING, 25.0)

    assert abs(heating_cop - 3.0) < 1e-9
    assert abs(cooling_cop - 4.0) < 1e-9