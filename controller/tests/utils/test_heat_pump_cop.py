from unittest.mock import MagicMock, patch

from controller.utils import utils


def test_heat_pump_specifications_are_fetched_once_for_repeated_evaluations(monkeypatch):
    monkeypatch.setenv("HEMS_API_BASE_URL", "http://hems.example.test")
    monkeypatch.setenv("HEAT_PUMP_MODEL", "test-model")
    utils._get_heat_pump_specifications.cache_clear()

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

    with patch("controller.utils.utils.requests.get", return_value=response) as get:
        cold_cop = utils.get_heat_pump_cop(utils.ControlMode.HEATING, -5.0)
        warm_cop = utils.get_heat_pump_cop(utils.ControlMode.HEATING, 5.0)

    assert cold_cop != warm_cop
    get.assert_called_once_with(
        "http://hems.example.test/api/devices/specifications/test-model", verify=False, timeout=10
    )
    utils._get_heat_pump_specifications.cache_clear()