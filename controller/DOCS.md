# Home Assistant Add-on: Controller

## What it does

This add-on controls heat pump and thermostats based on preferences set using the In-Home-Display.

## Safe edge testing

The add-on starts in `shadow` mode by default. It reads Home Assistant and HEMS data, listens for configured thermostat
and heat-pump setpoint changes, logs proposed actions without calling Home Assistant climate services, and stores each
proposal in TimescaleDB's `space_heating` table with `metric_type: control_shadow`. These rows are separate from
`control` rows written for live actions.

To verify event handling, change a configured climate entity's setpoint from its physical controls or the Home
Assistant dashboard. Confirm the add-on logs `HA setpoint changed` and a subsequent `SHADOW MODE` proposal. A physical
change is observable only if the device integration reports the updated setpoint to Home Assistant.

The add-on options `hems_poll_seconds` (default 30 seconds), `ha_reconciliation_seconds`, and
`hems_max_snapshot_age_seconds` configure the HEMS refresh cadence, periodic HA reconciliation, and maximum age of
cached HEMS data. A stale snapshot suppresses control evaluation. `HEMS_POLL_SECONDS` can override the add-on poll
setting through the process environment.

The `hems_data_source` app option explicitly selects `api` (default) or `mock`. In mock mode, place JSON mock files in
the shared `/share` folder and set `mock_configuration_path`, `mock_gdp_events_path`, and, when testing zones,
`mock_heat_pump_specifications_path` to their container paths. The configuration file must contain the HEMS
device-configuration object, the GDP file must contain a JSON event array (`[]` represents no events), and the
specifications file must contain the heat-pump specifications object returned by HEMS. Mock mode requires the device
configuration and GDP files, uses mock specifications when zone COP is evaluated, and automatically suppresses HEMS
status posts. HA entity states and setpoint events remain live. Paths and source selection are read at process startup;
restart the app after changing them.

The runtime-shaped example is [`device-configuration.mock.example.json`](../config/controller/device-configuration.mock.example.json).
It includes the required `hub.<lowercase building_id>` control-mode entry, a weekly thermostat schedule, a timestamped
HEMS parameter setpoint, and GDP flexibility settings. Copy it to the shared folder and set
`mock_configuration_path` to its container path. Replace the example building ID with the add-on's `building_id`, and
keep the parameter timestamp representative of when that HEMS value was actually updated; refreshing the file does
not update this timestamp. The example timestamp is fixed for illustration and should be changed for a later test.

HA thermostat setpoint events carry the entity's `last_updated` timestamp into target resolution. The controller
compares that timestamp with a HEMS parameter-setpoint timestamp (not the poll time) and uses the newest eligible
override. During a GDP preconditioning/reduction/recovery window, a manual override is eligible only if it changed
after the window began; that override then supersedes the remaining phases of that event. Earlier changes do not
cancel GDP. In live mode, events matching the controller's own requested setpoint are ignored as feedback.

Each received HA setpoint transition is also written to TimescaleDB as `metric_type: state_change`, with paired
`setpoint_old` and `setpoint_new` rows at the HA event's `last_updated` timestamp. If HA provides no numeric old value,
only the new-value row is written. These database writes run on a worker so they do not block WebSocket event reading.
Telegraf's periodic setpoint measurements remain enabled to provide baseline samples and coverage across listener
disconnects or restarts. To inspect transitions, filter `space_heating` by `metric_type = 'state_change'` and order by
`time`; the old and new rows for one transition share the same timestamp and device ID.

Choose `shadow` or `live` from the add-on's constrained `control_mode` selector. In live mode, `control_allowlist` must
be either a comma-separated list of entity IDs or the explicit value `all`. An empty value prevents live startup. A list
restricts actuation to those IDs; `all` allows every action generated from the HEMS device configuration, including
devices added later. It does not target every entity in Home Assistant, only entities present in the controller's action
map. Treat `all` as an unrestricted live-control opt-in and verify proposals in shadow mode before enabling it.