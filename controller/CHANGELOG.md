## 1.1.24

- Record HA setpoint changes in TimescaleDB as timestamped old/new `state_change` rows

## 1.1.23

- Restrict the add-on control mode option to the `shadow` and `live` choices

## 1.1.22

- Persist shadow control proposals to TimescaleDB with a distinct `control_shadow` metric type

## 1.1.21

- Use the newest timestamped HEMS or HA setpoint override
- Let an in-window HA setpoint override cancel the remaining phases of the current GDP event

## 1.1.20

- Add explicit API or mock-file HEMS data source selection for offline shadow testing
- Allow disabling HEMS status metrics during offline tests

## 1.1.19

- Make the event-driven controller the add-on runtime
- Default to shadow mode and require an entity allowlist for live control
- Reconcile HA setpoints from events and poll HEMS on a configurable interval

## 1.1.18

- Enhanced logging format

## 1.1.17

- Fixed bug in controller logic to retrieve the right device configuration

## 1.1.16

- Fixed heat pump control mode

## 1.1.15

- Fixed typo in heat pump control logic

## 1.1.14

- Pinned base Docker image version
- Extracted controller logic in a single file per device type

## 1.1.13

- Refactored heat pump control logic

## 1.1.12

- Now getting heatpump specifications from backend

## 1.1.11

- Moved heatpump automated control flag from HA add-on parameter to the UI

## 1.1.10

- Changed heatpump threshold for heating to 18 degrees

## 1.1.9

- Small fixes

## 1.1.8

- Fixed column names for local save of control action

## 1.1.7

- Fixed local save of control action

## 1.1.6

- Fixed peak events URL

## 1.1.5

- Changed controller logic

## 1.1.4

- Extracted peak events and heat pump configurations
- Simplified control logic

## 1.1.3

- Enable changing the indoor temperature sensor source
- Record user preferences and controller decisions for analytics and insights

## 1.1.2

- Improve save_control_actions so it reliably identifies and records heat‑pump off events.

## 1.1.1

- Fix to correctly use Heat-pump enable option in add-on configuration

## 1.1.0

- Add heat-pump enable option in add-on configuration
- Fix some issues with Hydro-Québec API

## 1.0.0

- Initial release
