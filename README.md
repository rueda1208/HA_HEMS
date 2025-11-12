# Home Energy Management System (HEMS)

This repository contains the HEMS (Home Energy Management System) implementation and related deployment artifacts.

The repo is structured to hold the main control service, configurations, container build scripts and helpers for telemetry (telegraf) and time-series storage (TimescaleDB).

## Repository layout

- `control/` — operational control artifacts and docs for deployments.
- `src/` — main source code and service implementation. The primary service is `ha_hems_control` located at `src/ha_hems_control`.
- `telegraf/` — telegraf configuration and helpers for metrics collection.
- `timescaledb/` — TimescaleDB helper scripts and configuration.
- `docker/` — repository-level Docker build scripts and helpers.
- `logs/` — runtime logs (gitignored in most setups).
- `secrets/` — secrets templates and example `config.yaml` (ensure real secrets are stored securely).
- `translations/` and `src/translations/` — translation files (e.g. `en.yaml`).

Key files:

- `pyproject.toml`, `poetry.toml` — Python packaging and dependency management.
- `src/ha_hems_control/main.py` — service entry point.
- `src/ha_hems_control/ha_interface/ha_interface.py` — Home Assistant / integration interface.
- `control/config.yaml`, `src/config.yaml`, `timescaledb/config.yaml` — configuration examples.

## Quick start (developer)

Prerequisites:

- Python 3.10+ (use the version declared in `pyproject.toml`)
- Poetry (optional, recommended for local dev)
- Docker (for container builds and some runtimes)

Run the main service locally (from repo root):

```bash
# from repository root
cd src
poetry install     # or install requirements into a venv
poetry run python -m ha_hems_control.main
```

If you don't use Poetry, create a virtual environment and run:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt  # if present / or use poetry export
python -m ha_hems_control.main
```

Run with Docker:

- Use the top-level docker build script to build images where applicable:

```bash
./docker/build.sh
```

Some subfolders (e.g. `telegraf/`, `timescaledb/`) also include `run.sh` scripts for quick local runs or containerized setups.

## Configuration

Configuration files live in multiple places; preferences are:

- `control/config.yaml` — control-plane configuration and deployment parameters.
- `src/config.yaml` — configuration consumed by the Python service.
- `secrets/config.yaml` — example secret values (do not commit real secrets).

When deploying, ensure the service can read the appropriate configuration (via volume mounts or environment variables).

## Logging & telemetry

- Runtime logs are written to `logs/` by default (check service config to confirm paths).
- Telegraf configurations are in `telegraf/` to collect metrics and forward to TimescaleDB or InfluxDB backends.

## Translations

English translations are located at `translations/en.yaml` and `src/translations/en.yaml` for service-level messages.

## Development notes

- Follow the project's `pyproject.toml` for dependency versions.
- Use the `src/ha_hems_control` package layout for edits. Entry point is `main.py`.

## Contributing

Please open issues or PRs against the `main` branch. Keep changes small and test locally before submitting.

## License

See `LICENSE` at the repository root.

---

If you'd like, I can also:

- add a short `Makefile` or `dev/` scripts to simplify common developer flows (venv, run, lint),
- generate a minimal `requirements.txt` exported from Poetry for users who don't use Poetry.

Contact: maintainers and contributors are listed in the repo metadata.


[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
[armhf-shield]: https://img.shields.io/badge/armhf-yes-green.svg
[armv7-shield]: https://img.shields.io/badge/armv7-yes-green.svg
[i386-shield]: https://img.shields.io/badge/i386-yes-green.svg