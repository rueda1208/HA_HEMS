from datetime import datetime, timedelta, timezone

from controller.base import ControlContext, SwitchControlAction
from controller.devices.water_heater import WaterHeaterController
from controller.utils import utils
from controller.utils.peak_events import PeakEvent


EVENT_START = datetime(2026, 10, 14, 16, tzinfo=timezone.utc)
EVENT_END = EVENT_START + timedelta(hours=4)
DEVICE_ID = "water_heater.chauffe_eau"
SWITCH_ID = "switch.chauffe_eau"


def make_event() -> PeakEvent:
    return PeakEvent(
        offre="CPC-D",
        plagehoraire="PM",
        duree="PT04H00MS",
        secteurclient="Residentiel",
        datedebut=EVENT_START,
        datefin=EVENT_END,
    )


def make_context(now: datetime, state: str, last_changed: datetime, event: PeakEvent | None = None) -> ControlContext:
    return ControlContext(
        device_id=DEVICE_ID,
        device_configuration={"device_type": "water_heater", "switch_entity_id": SWITCH_ID},
        all_devices_configurations={},
        devices_states={
            SWITCH_ID: {
                "state": state,
                "last_changed": last_changed.isoformat(),
            }
        },
        control_mode=utils.ControlMode.HEATING,
        gdp_event=event,
        now=now,
        gdp_device_ids=frozenset({DEVICE_ID}),
    )


def test_water_heater_is_unchanged_during_preconditioning():
    controller = WaterHeaterController()
    context = make_context(
        EVENT_START - timedelta(minutes=30),
        "on",
        EVENT_START - timedelta(hours=1),
        make_event(),
    )

    assert controller.get_control_actions(context) == {}


def test_water_heater_is_turned_off_during_event_if_on_before_event():
    controller = WaterHeaterController()
    context = make_context(
        EVENT_START + timedelta(minutes=1),
        "on",
        EVENT_START - timedelta(minutes=1),
        make_event(),
    )

    assert controller.get_control_actions(context) == {
        SWITCH_ID: SwitchControlAction(desired_state="off", reason="gdp_event_reduction")
    }


def test_on_transition_during_event_cancels_gdp_for_rest_of_event():
    controller = WaterHeaterController()
    event = make_event()
    controller.get_control_actions(
        make_context(
            EVENT_START + timedelta(minutes=1),
            "off",
            EVENT_START - timedelta(hours=1),
            event,
        )
    )

    controller.cancel_gdp_for_event(DEVICE_ID, event)

    assert controller.get_control_actions(
        make_context(
            EVENT_START + timedelta(minutes=2),
            "on",
            EVENT_START + timedelta(minutes=2),
            event,
        )
    ) == {}
    assert controller.get_control_actions(
        make_context(
            EVENT_START + timedelta(minutes=3),
            "off",
            EVENT_START + timedelta(minutes=3),
            event,
        )
    ) == {}


def test_on_transition_during_preconditioning_does_not_cancel_event():
    controller = WaterHeaterController()
    event = make_event()
    context = make_context(
        EVENT_START + timedelta(minutes=1),
        "on",
        EVENT_START - timedelta(minutes=1),
        event,
    )

    assert controller.get_control_actions(context) == {
        SWITCH_ID: SwitchControlAction(desired_state="off", reason="gdp_event_reduction")
    }


def test_water_heater_is_restored_to_on_at_event_end():
    controller = WaterHeaterController()
    event = make_event()
    controller.get_control_actions(
        make_context(EVENT_START + timedelta(minutes=1), "off", EVENT_START, event)
    )

    assert controller.get_control_actions(
        make_context(EVENT_END, "off", EVENT_START, event)
    ) == {
        SWITCH_ID: SwitchControlAction(desired_state="on", reason="gdp_event_end")
    }


def test_restart_during_event_respects_on_state_changed_during_event():
    controller = WaterHeaterController()
    context = make_context(
        EVENT_START + timedelta(hours=1),
        "on",
        EVENT_START + timedelta(minutes=10),
        make_event(),
    )

    assert controller.get_control_actions(context) == {}


def test_switch_off_after_event_end_is_not_overridden():
    controller = WaterHeaterController()
    event = make_event()
    controller.get_control_actions(
        make_context(EVENT_START + timedelta(minutes=1), "off", EVENT_START, event)
    )
    context = make_context(
        EVENT_END + timedelta(minutes=1),
        "off",
        EVENT_END + timedelta(minutes=1),
        event,
    )

    assert controller.get_control_actions(context) == {}


def test_event_removal_after_end_still_restores_switch():
    controller = WaterHeaterController()
    event = make_event()
    controller.get_control_actions(
        make_context(EVENT_START + timedelta(minutes=1), "off", EVENT_START, event)
    )

    assert controller.get_control_actions(
        make_context(EVENT_END + timedelta(hours=2), "off", EVENT_START, None)
    ) == {
        SWITCH_ID: SwitchControlAction(desired_state="on", reason="gdp_event_end")
    }


def test_unselected_water_heater_is_not_controlled():
    context = make_context(
        EVENT_START + timedelta(minutes=1),
        "on",
        EVENT_START - timedelta(minutes=1),
        make_event(),
    )
    context.gdp_device_ids = frozenset()

    assert WaterHeaterController().get_control_actions(context) == {}
