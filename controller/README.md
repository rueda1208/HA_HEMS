# Home Assistant Controller

This package is the Home Assistant add-on controller. The add-on entry point is `controller.main`; `run.sh` starts
that same module.

## Development

From this directory, install the project and development dependencies, then run the service or tests:

```bash
poetry install --with dev
poetry run python -m controller.main
poetry run pytest
```

## Edge testing

The add-on defaults to `control_mode: shadow`, which logs proposed actions without issuing climate service calls. Change
a configured thermostat or heat-pump setpoint physically or through Home Assistant and verify the `HA setpoint changed`
log followed by a shadow proposal. See [DOCS.md](DOCS.md) for the live allowlist and polling settings. Keep shadow mode
enabled until event delivery and proposals are verified on the edge device.