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