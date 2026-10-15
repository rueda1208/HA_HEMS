import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from controller.base import SetpointOverride
from controller.utils.target_temperature import TargetTemperatureSource, resolve_target_temperature
from controller.utils.peak_event_plan import GdpPhase, GdpProfileName
from controller.utils.configuration import retrieve_device_configuration
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

    assert retrieve_device_configuration() == configuration


def test_realistic_mock_configuration_example_supports_gdp_selection_and_manual_override(monkeypatch):
    configuration_path = (
        Path(__file__).resolve().parents[3] / "config" / "controller" / "device-configuration.mock.example.json"
    )
    monkeypatch.setenv("HEMS_DATA_SOURCE", "mock")
    monkeypatch.setenv("MOCK_CONFIGURATION_PATH", str(configuration_path))
    monkeypatch.setenv("BUILDING_ID", "HEMS201")

    configuration = retrieve_device_configuration()
    assert configuration["hub.hems201"]["mode"]["value"] == "heating"
    assert configuration["hub.hems201"]["gdp_profile"]["value"] == "moderate"
    selected_gdp_devices = configuration["hub.hems201"]["gdp_device_ids"]["value"]
    assert isinstance(selected_gdp_devices, list)
    assert any(item["device_id"] == "switch.chauffe_eau" for item in selected_gdp_devices)
    assert configuration["switch.chauffe_eau"]["device_type"] == "water_heater"
    assert configuration["switch.chauffe_eau"]["switch_entity_id"] == "switch.chauffe_eau"
    assert configuration["climate.sous_sol_2"]["setpoint"]["source"] == "default_parameter"

    resolution = resolve_target_temperature(
        "climate.sous_sol_2",
        configuration["climate.sous_sol_2"],
        gdp_event=None,
        now=datetime.fromisoformat("2026-10-08T23:45:00-04:00"),
        ha_setpoint_override=SetpointOverride(
            21.5,
            datetime.fromisoformat("2026-10-08T23:33:15-04:00"),
        ),
    )

    assert resolution.value == 21.5
    assert resolution.source == TargetTemperatureSource.HA_MANUAL_OVERRIDE


def test_runtime_mock_configuration_selects_water_heater_for_gdp():
    configuration_path = Path(__file__).resolve().parents[3] / "examples" / "runtime" / "config-play.json"
    configuration = json.loads(configuration_path.read_text(encoding="utf-8"))

    assert configuration["switch.chauffe_eau"] == {
        "device_type": "water_heater",
        "switch_entity_id": "switch.chauffe_eau",
    }
    assert any(
        item["device_id"] == "switch.chauffe_eau"
        for item in configuration["hub.hems201"]["gdp_device_ids"]["value"]
    )


def test_empty_mock_gdp_file_avoids_hems_api_request(tmp_path, monkeypatch):
    events_path = tmp_path / "peak-events.json"
    events_path.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("HEMS_DATA_SOURCE", "mock")
    monkeypatch.setenv("MOCK_GDP_EVENTS_PATH", str(events_path))

    assert utils.retrieve_gdp_event() is None


def test_mock_gdp_event_contains_only_event_data(tmp_path, monkeypatch):
    now = datetime.now().astimezone()
    events_path = tmp_path / "peak-events.json"
    events_path.write_text(
        json.dumps(
            [
                {
                    "offre": "tarif",
                    "plagehoraire": "pointe",
                    "duree": "180",
                    "secteurclient": "residentiel",
                    "datedebut": (now + timedelta(hours=1)).isoformat(),
                    "datefin": (now + timedelta(hours=3)).isoformat(),
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HEMS_DATA_SOURCE", "mock")
    monkeypatch.setenv("MOCK_GDP_EVENTS_PATH", str(events_path))

    event = utils.retrieve_gdp_event()

    assert event is not None
    assert event.datedebut.date() == now.date()
    assert event.datefin > event.datedebut


def test_retrieves_ended_event_only_during_configured_recovery_window(tmp_path, monkeypatch):
    now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    fixed_now = [now]

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now[0].astimezone().replace(tzinfo=None)
            return fixed_now[0].astimezone(tz)

    monkeypatch.setattr(utils.datetime, "datetime", FrozenDateTime)
    event_start = now - timedelta(hours=5)
    event_end = now - timedelta(minutes=30)
    events_path = tmp_path / "peak-events.json"
    events_path.write_text(
        json.dumps(
            [
                {
                    "offre": "tarif",
                    "plagehoraire": "pointe",
                    "duree": "180",
                    "secteurclient": "residentiel",
                    "datedebut": event_start.isoformat(),
                    "datefin": event_end.isoformat(),
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HEMS_DATA_SOURCE", "mock")
    monkeypatch.setenv("MOCK_GDP_EVENTS_PATH", str(events_path))
    monkeypatch.setenv("BUILDING_ID", "TestBuilding")
    device_id = "climate.thermostat_1"
    configurations = {
        "hub.testbuilding": {
            "mode": {"value": "heating"},
            "gdp_profile": {"value": "bold"},
        },
        device_id: {"device_type": "thermostat", "setpoint": {"value": 21}},
    }

    event = utils.retrieve_gdp_event(configurations)
    assert event is not None
    assert event.datedebut == event_start

    resolution = resolve_target_temperature(
        device_id,
        configurations[device_id],
        event,
        now,
        gdp_profile_name=GdpProfileName.BOLD,
    )
    assert resolution.source == TargetTemperatureSource.GDP_EVENT
    assert resolution.gdp_phase == GdpPhase.RECOVERY

    later_now = event_end + timedelta(minutes=59)
    fixed_now[0] = later_now
    for profile_name in ("extreme", "bold", "moderate"):
        configurations["hub.testbuilding"]["gdp_profile"]["value"] = profile_name
        assert utils.retrieve_gdp_event(configurations) is not None

    fixed_now[0] = event_end + timedelta(hours=1)
    for profile_name in ("extreme", "bold", "moderate"):
        configurations["hub.testbuilding"]["gdp_profile"]["value"] = profile_name
        assert utils.retrieve_gdp_event(configurations) is None


def test_upcoming_event_preconditioning_takes_precedence_over_older_recovery(tmp_path, monkeypatch):
    now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    fixed_now = [now]

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now[0].astimezone().replace(tzinfo=None)
            return fixed_now[0].astimezone(tz)

    monkeypatch.setattr(utils.datetime, "datetime", FrozenDateTime)
    previous_event = {
        "offre": "tarif",
        "plagehoraire": "AM",
        "duree": "180",
        "secteurclient": "residentiel",
        "datedebut": (now - timedelta(hours=5)).isoformat(),
        "datefin": (now - timedelta(minutes=30)).isoformat(),
    }
    upcoming_start = now + timedelta(hours=1)
    upcoming_event = {
        "offre": "tarif",
        "plagehoraire": "PM",
        "duree": "180",
        "secteurclient": "residentiel",
        "datedebut": upcoming_start.isoformat(),
        "datefin": (upcoming_start + timedelta(hours=3)).isoformat(),
    }
    events_path = tmp_path / "peak-events.json"
    events_path.write_text(json.dumps([previous_event, upcoming_event]), encoding="utf-8")
    monkeypatch.setenv("HEMS_DATA_SOURCE", "mock")
    monkeypatch.setenv("MOCK_GDP_EVENTS_PATH", str(events_path))
    monkeypatch.setenv("BUILDING_ID", "TestBuilding")
    configurations = {
        "hub.testbuilding": {
            "mode": {"value": "heating"},
            "gdp_profile": {"value": "bold"},
        }
    }

    event = utils.retrieve_gdp_event(configurations)

    assert event is not None
    assert event.datedebut == upcoming_start


def test_mock_source_fails_clearly_when_a_required_file_is_missing(monkeypatch):
    monkeypatch.setenv("HEMS_DATA_SOURCE", "mock")
    monkeypatch.delenv("MOCK_CONFIGURATION_PATH", raising=False)

    try:
        retrieve_device_configuration()
    except ValueError as error:
        assert "MOCK_CONFIGURATION_PATH is required" in str(error)
    else:
        raise AssertionError("Expected missing mock configuration path to fail")