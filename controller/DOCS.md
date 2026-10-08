# Home Assistant Add-on: Controller

## What it does

This add-on controls heat pump and thermostats based on preferences set using the In-Home-Display.

## Safe edge testing

The add-on starts in `shadow` mode by default. It reads Home Assistant and HEMS data, listens for configured thermostat
and heat-pump setpoint changes, and logs proposed actions without calling Home Assistant climate services.

To verify event handling, change a configured climate entity's setpoint from its physical controls or the Home
Assistant dashboard. Confirm the add-on logs `HA setpoint changed` and a subsequent `SHADOW MODE` proposal. A physical
change is observable only if the device integration reports the updated setpoint to Home Assistant.

The add-on options `hems_poll_seconds` (default 30 seconds), `ha_reconciliation_seconds`, and
`hems_max_snapshot_age_seconds` configure the HEMS refresh cadence, periodic HA reconciliation, and maximum age of
cached HEMS data. A stale snapshot suppresses control evaluation. `HEMS_POLL_SECONDS` can override the add-on poll
setting through the process environment.

Live control must be enabled explicitly with `control_mode: live` and a non-empty comma-separated
`control_allowlist` of entity IDs. Start with one test entity and verify behavior before expanding the allowlist.