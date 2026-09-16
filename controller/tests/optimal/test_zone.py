from unittest.mock import MagicMock

from controller.optimal.zone import ZoneController, ZoneControlParameters


def test_zone_control_parameters_defaults():
    params = ZoneControlParameters.from_device_configuration({})

    assert params.kp == 0.35
    assert params.max_heat_push == 1.5
    assert params.max_cool_push == -2.0
    assert params.heat_pump_calibration_offset == 2.0


def test_zone_control_parameters_applies_overrides():
    device_configuration = {"kp": {"value": "0.5"}, "max_heat_push": {"value": "1.0"}}
    params = ZoneControlParameters.from_device_configuration(device_configuration)

    assert params.kp == 0.5
    assert params.max_heat_push == 1.0
    assert params.max_cool_push == -2.0  # untouched default


def test_compute_heating_adjustment_cool_zone_pushes_heat_up():
    zone_controller = ZoneController(db_engine=MagicMock())
    params = ZoneControlParameters()

    adjustment = zone_controller._compute_heating_adjustment(
        target_temperature=21.0, indoor_temperature=20.0, temp_trend=None, heat_pump_cop=2.5, params=params
    )

    assert adjustment > 0


def test_compute_heating_adjustment_hot_zone_pushes_heat_down():
    zone_controller = ZoneController(db_engine=MagicMock())
    params = ZoneControlParameters()

    adjustment = zone_controller._compute_heating_adjustment(
        target_temperature=21.0, indoor_temperature=22.0, temp_trend=None, heat_pump_cop=2.5, params=params
    )

    assert adjustment < 0


def test_compute_heating_adjustment_clamped_to_max_push():
    zone_controller = ZoneController(db_engine=MagicMock())
    params = ZoneControlParameters(max_heat_push=0.1, max_cool_push=-0.1)

    adjustment = zone_controller._compute_heating_adjustment(
        target_temperature=25.0, indoor_temperature=15.0, temp_trend=None, heat_pump_cop=1.0, params=params
    )

    assert adjustment == params.max_heat_push
