import logging
import os

from datetime import datetime
from typing import Any, Dict

from sqlalchemy import Engine

from controller.base import ControlContext, DeviceController, SetpointOverride
from controller.devices import (
    BatteryController,
    ElectricVehicleController,
    HeatPumpController,
    ThermostatController,
    WaterHeaterController,
    ZoneController,
)
from controller.utils import utils
from controller.utils.configuration import parse_gdp_device_selection
from controller.utils.device_type import DeviceType
from controller.utils.peak_event_plan import (
    GdpPhase,
    GdpProfileName,
    GdpResponseProfile,
    PeakEventPlan,
    normalize_gdp_profile_name,
)
from controller.utils.peak_events import PeakEvent


logger = logging.getLogger(__name__)


class Controller:
    def __init__(self, db_engine: Engine) -> None:
        self._device_controllers: Dict[DeviceType, DeviceController] = {
            DeviceType.ZONE: ZoneController(db_engine),
            DeviceType.HEAT_PUMP: HeatPumpController(),
            DeviceType.THERMOSTAT: ThermostatController(),
            DeviceType.BATTERY: BatteryController(),
            DeviceType.ELECTRIC_VEHICLE: ElectricVehicleController(),
            DeviceType.WATER_HEATER: WaterHeaterController(),
        }
        self._last_logged_gdp_context: tuple[Any, ...] | None = None
        self._last_logged_gdp_phases: dict[str, tuple[tuple[str, ...], GdpPhase]] = {}

    def get_control_actions(
        self,
        devices_states: Dict[str, Any],
        configurations: Dict[str, Any],
        gdp_event: PeakEvent | None,
        ha_setpoint_overrides: Dict[str, SetpointOverride] | None = None,
    ) -> Dict[str, Any]:
        building_id = str(os.getenv("BUILDING_ID"))

        hub_configuration = configurations.get(f"hub.{building_id.lower()}", {})
        control_mode_str = hub_configuration.get("mode", {}).get("value", "off")
        control_mode = self._get_control_mode_from_string(control_mode_str)
        if control_mode == utils.ControlMode.OFF:
            logger.info("Controller is in OFF mode, skipping control actions")
            return {}

        # TODO: Confirm these value wrappers and whether HEMS uses controller configuration IDs here.
        profile_value = hub_configuration.get("gdp_profile", {}).get("value", GdpProfileName.MODERATE.value)
        try:
            gdp_profile_name = normalize_gdp_profile_name(profile_value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Unsupported GDP profile in hub configuration: {profile_value}") from error

        raw_gdp_device_ids = hub_configuration.get("gdp_device_ids", {}).get("value")
        gdp_device_ids, gdp_preconditioning_by_device = parse_gdp_device_selection(
            raw_gdp_device_ids, configurations
        )

        now = datetime.now().astimezone()
        self._log_gdp_context(
            now,
            gdp_event,
            profile_value,
            gdp_profile_name,
            gdp_device_ids,
            gdp_preconditioning_by_device,
            configurations,
        )
        self._log_gdp_phase_transitions(
            now,
            gdp_event,
            gdp_profile_name,
            gdp_device_ids,
            gdp_preconditioning_by_device,
            configurations,
        )

        if gdp_event:
            logger.info("GDP event detected, adjusting control strategy accordingly")
        else:
            logger.info("No GDP event detected, proceeding with normal control strategy")

        control_actions: Dict[str, Any] = {}

        for device_id, configuration in configurations.items():
            device_type = configuration.get("device_type")
            if device_type == DeviceType.HUB:
                logger.debug(f"No control actions required for device of type hub: {device_id}")
            else:
                device_controller = self._device_controllers.get(device_type)
                if device_controller is None:
                    logger.info(f"Ignoring control actions for device: {device_id}")
                    continue

                context = ControlContext(
                    device_id=device_id,
                    device_configuration=configuration,
                    all_devices_configurations=configurations,
                    devices_states=devices_states,
                    control_mode=control_mode,
                    gdp_event=gdp_event,
                    now=now,
                    ha_setpoint_override=(ha_setpoint_overrides or {}).get(device_id),
                    gdp_profile_name=gdp_profile_name,
                    gdp_device_ids=gdp_device_ids,
                    gdp_preconditioning_by_device=gdp_preconditioning_by_device,
                )
                control_actions.update(device_controller.get_control_actions(context))

        return control_actions

    def cancel_water_heater_gdp(self, device_id: str, event: PeakEvent) -> None:
        water_heater_controller = self._device_controllers[DeviceType.WATER_HEATER]
        if isinstance(water_heater_controller, WaterHeaterController):
            water_heater_controller.cancel_gdp_for_event(device_id, event)

    def _log_gdp_context(
        self,
        now: datetime,
        gdp_event: PeakEvent | None,
        configured_profile: Any,
        profile_name: GdpProfileName,
        gdp_device_ids: frozenset[str],
        preconditioning_by_device: dict[str, bool],
        configurations: Dict[str, Any],
    ) -> None:
        event_details = None
        event_signature = None
        if gdp_event is not None:
            event_details = {
                "offre": gdp_event.offre,
                "plagehoraire": gdp_event.plagehoraire,
                "secteurclient": gdp_event.secteurclient,
                "duree": gdp_event.duree,
                "datedebut": gdp_event.datedebut.isoformat(),
                "datefin": gdp_event.datefin.isoformat(),
            }
            event_signature = tuple(event_details.items())

        effective_device_profiles = {}
        profile_signature = []
        temperature_device_types = {DeviceType.THERMOSTAT, DeviceType.HEAT_PUMP, DeviceType.ZONE}
        if gdp_event is not None:
            for device_id in sorted(gdp_device_ids):
                configuration = configurations.get(device_id)
                if (
                    not isinstance(configuration, dict)
                    or configuration.get("device_type") not in temperature_device_types
                ):
                    continue

                profile = GdpResponseProfile.from_device_configuration(
                    profile_name,
                    configuration,
                    preconditioning_override=preconditioning_by_device.get(device_id),
                )
                details = {
                    "preconditioning": profile.preconditioning_enabled,
                    "preconditioning_lead_minutes": profile.preconditioning_lead_time.total_seconds() / 60,
                    "recovery_minutes": profile.recovery_duration.total_seconds() / 60,
                    "flexibility_upward": profile.flexibility_upward,
                    "flexibility_downward": profile.flexibility_downward,
                }
                effective_device_profiles[device_id] = details
                profile_signature.append((device_id, tuple(details.items())))

        context_signature = (
            event_signature,
            str(configured_profile),
            profile_name.value,
            tuple(sorted(gdp_device_ids)),
            tuple(sorted(preconditioning_by_device.items())),
            tuple(profile_signature),
        )
        if context_signature == self._last_logged_gdp_context:
            return

        logger.info(
            "GDP context changed: observed_at=%s event=%s configured_profile=%s normalized_profile=%s "
            "gdp_device_ids=%s per_device_profiles=%s preconditioning_overrides=%s",
            now.isoformat(),
            event_details,
            configured_profile,
            profile_name.value,
            sorted(gdp_device_ids),
            effective_device_profiles,
            dict(sorted(preconditioning_by_device.items())),
        )
        self._last_logged_gdp_context = context_signature

    def _log_gdp_phase_transitions(
        self,
        now: datetime,
        gdp_event: PeakEvent | None,
        profile_name: GdpProfileName,
        gdp_device_ids: frozenset[str],
        preconditioning_by_device: dict[str, bool],
        configurations: Dict[str, Any],
    ) -> None:
        current_event_key = gdp_event.identity_key if gdp_event is not None else None

        temperature_device_types = {DeviceType.THERMOSTAT, DeviceType.HEAT_PUMP, DeviceType.ZONE}
        selected_temperature_devices = {
            device_id
            for device_id in gdp_device_ids
            if isinstance(configurations.get(device_id), dict)
            and configurations[device_id].get("device_type") in temperature_device_types
        }

        for device_id, (previous_event, previous_phase) in list(self._last_logged_gdp_phases.items()):
            if device_id in selected_temperature_devices and previous_event == current_event_key:
                continue
            if previous_phase != GdpPhase.NORMAL:
                logger.info(
                    "GDP phase transition: device=%s event_start=%s event_end=%s previous_phase=%s "
                    "phase=normal observed_at=%s reason=%s",
                    device_id,
                    previous_event[3],
                    previous_event[4],
                    previous_phase.value,
                    now.isoformat(),
                    "event_not_selected" if current_event_key is None else "event_or_device_changed",
                )
            del self._last_logged_gdp_phases[device_id]

        if gdp_event is None:
            return

        event_key = gdp_event.identity_key
        for device_id in sorted(selected_temperature_devices):
            configuration = configurations[device_id]
            profile = GdpResponseProfile.from_device_configuration(
                profile_name,
                configuration,
                preconditioning_override=preconditioning_by_device.get(device_id),
            )
            plan = PeakEventPlan(event=gdp_event, profile=profile)
            phase = plan.phase_at(now)
            previous = self._last_logged_gdp_phases.get(device_id)
            previous_phase = previous[1] if previous is not None else GdpPhase.NORMAL

            if previous_phase != phase:
                phase_windows = {
                    GdpPhase.PRECONDITIONING: plan.preconditioning_window,
                    GdpPhase.REDUCTION: plan.reduction_window,
                    GdpPhase.RECOVERY: plan.recovery_window,
                }
                phase_window = phase_windows.get(phase)
                logger.info(
                    "GDP phase transition: device=%s event_start=%s event_end=%s profile=%s "
                    "previous_phase=%s phase=%s phase_window_start=%s observed_at=%s",
                    device_id,
                    gdp_event.datedebut.isoformat(),
                    gdp_event.datefin.isoformat(),
                    profile.name.value,
                    previous_phase.value,
                    phase.value,
                    phase_window[0].isoformat() if phase_window is not None else None,
                    now.isoformat(),
                )

            self._last_logged_gdp_phases[device_id] = (event_key, phase)

    def _get_control_mode_from_string(self, control_mode_str: str) -> utils.ControlMode:
        try:
            return utils.ControlMode(control_mode_str)
        except ValueError:
            logger.warning(f"Invalid control mode '{control_mode_str}' in configuration, defaulting to OFF")
            return utils.ControlMode.OFF
