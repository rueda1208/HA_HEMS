#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
DRY_RUN=false
PERSISTENT=false

for argument in "$@"; do
  case "$argument" in
    --dry-run)
      DRY_RUN=true
      ;;
    --persistent)
      PERSISTENT=true
      ;;
    *)
      echo "Usage: $0 [--dry-run] [--persistent]" >&2
      exit 2
      ;;
  esac
done

if [[ "$DRY_RUN" != true ]]; then
  if [[ -z "${BASE_HA_URL:-}" ]]; then
    read -r -p "Reachable Home Assistant base URL (for example http://192.168.1.10:8123): " BASE_HA_URL
    export BASE_HA_URL
  fi
  if [[ -z "${SUPERVISOR_TOKEN_FILE:-}" && -r "$REPO_ROOT/secrets/ha_long_live.txt" ]]; then
    SUPERVISOR_TOKEN_FILE="$REPO_ROOT/secrets/ha_long_live.txt"
    export SUPERVISOR_TOKEN_FILE
  fi
  if [[ -n "${SUPERVISOR_TOKEN_FILE:-}" ]]; then
    if [[ ! -f "$SUPERVISOR_TOKEN_FILE" || ! -r "$SUPERVISOR_TOKEN_FILE" || ! -s "$SUPERVISOR_TOKEN_FILE" ]]; then
      echo "SUPERVISOR_TOKEN_FILE must point to a readable, non-empty token file." >&2
      exit 1
    fi
    SUPERVISOR_TOKEN_FILE="$(cd -- "$(dirname -- "$SUPERVISOR_TOKEN_FILE")" && pwd)/$(basename -- "$SUPERVISOR_TOKEN_FILE")"
    export SUPERVISOR_TOKEN_FILE
  elif [[ -z "${SUPERVISOR_TOKEN:-}" ]]; then
    read -r -s -p "Home Assistant long-lived access token: " SUPERVISOR_TOKEN
    printf '\n'
    export SUPERVISOR_TOKEN
  fi
fi

cd "$SCRIPT_DIR"
exec poetry run python - "$SCRIPT_DIR/config.yaml" "$SCRIPT_DIR/run.sh" "$REPO_ROOT" "$DRY_RUN" "$PERSISTENT" <<'PY'
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import yaml

config_path = Path(sys.argv[1])
script_dir = config_path.parent.resolve()
run_script_path = Path(sys.argv[2])
repo_root = Path(sys.argv[3])
dry_run = sys.argv[4] == "true"
persistent = sys.argv[5] == "true"
token_file_value = os.environ.get("SUPERVISOR_TOKEN_FILE")
token_file = Path(token_file_value).resolve() if token_file_value else None

with config_path.open(encoding="utf-8") as config_file:
    addon_config = yaml.safe_load(config_file)
with run_script_path.open(encoding="utf-8") as run_script_file:
    run_script = run_script_file.read()

options = addon_config.get("options", {})
fallbacks = dict(
    re.findall(r'(?m)^([A-Z][A-Z0-9_]*)="\$\{\1:-([^}]*)\}"\s*$', run_script)
)
config_mappings = re.findall(
    r"""(?m)^([A-Z][A-Z0-9_]*)="\$\(bashio::config '([^']+)'\)"\s*$""",
    run_script,
)

def env_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)

container_env = {
    str(name).upper(): env_value(value)
    for name, value in addon_config.get("environment", {}).items()
}
for variable, option in config_mappings:
    value = env_value(options.get(option))
    if not value:
        value = fallbacks.get(variable, "")
    if value:
        container_env[variable] = value

mock_path_variables = {
    "MOCK_CONFIGURATION_PATH",
    "MOCK_GDP_EVENTS_PATH",
    "MOCK_HEAT_PUMP_SPECIFICATIONS_PATH",
}
overridable_variables = (
    {variable for variable, _ in config_mappings} - mock_path_variables
) | set(container_env) | {
    "BASE_HA_URL",
    "BUILDING_ID",
    "HEAT_PUMP_MODEL",
    "ENVIRONMENT_SENSOR_ID",
    "WEATHER_ENTITY_ID",
    "HEMS_API_BASE_URL",
}
for variable in overridable_variables:
    if variable in os.environ:
        container_env[variable] = os.environ[variable]

control_mode = os.environ.get("LOCAL_CONTROL_MODE", "shadow").strip().lower()
if control_mode not in {"shadow", "live"}:
    raise SystemExit("LOCAL_CONTROL_MODE must be 'shadow' or 'live'.")
control_allowlist = os.environ.get("LOCAL_CONTROL_ALLOWLIST", "").strip()
if control_mode == "live" and not control_allowlist:
    raise SystemExit(
        "LOCAL_CONTROL_ALLOWLIST must name at least one entity when "
        "LOCAL_CONTROL_MODE=live."
    )
container_env["CONTROL_MODE"] = control_mode
container_env["CONTROL_ALLOWLIST"] = control_allowlist
container_env["HEMS_DATA_SOURCE"] = "mock"
container_env["HEMS_STATUS_METRICS_ENABLED"] = "false"
container_env["LOGS_DIR"] = "/share/controller/logs"

if not container_env.get("BUILDING_ID"):
    raise SystemExit("BUILDING_ID is missing from config.yaml and the environment.")
if not dry_run and not container_env.get("BASE_HA_URL"):
    raise SystemExit("BASE_HA_URL is required to connect to Home Assistant.")
if not dry_run:
    ha_url = urlparse(container_env["BASE_HA_URL"])
    if ha_url.scheme not in {"http", "https"} or not ha_url.netloc:
        raise SystemExit(
            "BASE_HA_URL must be a valid URL, for example "
            "'http://hems201.hydroquebec.lab:8123' (include only one http:// or https://)."
        )
if not dry_run and not token_file and not os.environ.get("SUPERVISOR_TOKEN"):
    raise SystemExit("SUPERVISOR_TOKEN is required to connect to Home Assistant.")
if not dry_run and token_file and (not token_file.is_file() or not os.access(token_file, os.R_OK)):
    raise SystemExit("SUPERVISOR_TOKEN_FILE must point to a readable token file.")

mock_sources = {
    "MOCK_CONFIGURATION_PATH": Path(
        os.environ.get(
            "LOCAL_MOCK_CONFIGURATION_FILE",
            repo_root
            / (
                "examples/runtime/config-play.json"
                if persistent
                else "config/controller/device-configuration.mock.example.json"
            ),
        )
    ),
    "MOCK_GDP_EVENTS_PATH": Path(
        os.environ.get(
            "LOCAL_MOCK_GDP_EVENTS_FILE",
            repo_root
            / (
                "examples/runtime/gdp-play.json"
                if persistent
                else "config/controller/peak-events.example.json"
            ),
        )
    ),
    "MOCK_HEAT_PUMP_SPECIFICATIONS_PATH": Path(
        os.environ.get(
            "LOCAL_MOCK_HEAT_PUMP_SPECIFICATIONS_FILE",
            repo_root / "config/controller/heat-pump.example.yaml",
        )
    ),
}

for variable, source in mock_sources.items():
    destination_value = container_env.get(variable)
    if not destination_value:
        raise SystemExit(f"{variable} is not configured by config.yaml/run.sh.")
    destination = Path(destination_value)
    try:
        relative_destination = destination.relative_to("/share")
    except ValueError as error:
        raise SystemExit(
            f"{variable} must point below /share for this local runner; "
            f"got {destination_value!r}."
        ) from error
    if not source.is_file():
        raise SystemExit(f"Mock source file does not exist: {source}")
    if variable == "MOCK_CONFIGURATION_PATH":
        with source.open(encoding="utf-8") as configuration_file:
            mock_configuration = json.load(configuration_file)
        expected_hub = f"hub.{container_env['BUILDING_ID'].lower()}"
        if expected_hub not in mock_configuration:
            raise SystemExit(
                f"{source} has no {expected_hub!r} entry; set BUILDING_ID "
                "to match the mock configuration."
            )
    mock_sources[variable] = (source, relative_destination)

db_container = os.environ.get("LOCAL_TIMESCALE_CONTAINER", "ha-hems-timescaledb")
db_host = os.environ.get("LOCAL_POSTGRES_HOST")
if not db_host:
    db_host = os.environ.get("POSTGRES_HOST", db_container)
if db_host in {"127.0.0.1", "localhost"}:
    db_host = db_container
container_env.update(
    {
        "POSTGRES_HOST": db_host,
        "POSTGRES_PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "POSTGRES_NAME": os.environ.get("POSTGRES_NAME", "homeassistant"),
        "POSTGRES_USER": os.environ.get("POSTGRES_USER", "postgres"),
        "POSTGRES_PASSWORD": os.environ.get("POSTGRES_PASSWORD", "homeassistant"),
    }
)

image = os.environ.get("LOCAL_CONTROLLER_IMAGE", "ha-hems-controller:local")
network = os.environ.get("LOCAL_DOCKER_NETWORK", "hems-local")
name = "ha-hems-controller-local" if persistent else f"ha-hems-controller-local-{os.getpid()}"

persistent_mock_mounts: list[tuple[Path, Path]] = []
if persistent:
    for index, (variable, (source, _)) in enumerate(mock_sources.items()):
        target_directory = Path("/share/local-mocks") / str(index)
        container_env[variable] = str(target_directory / source.name)
        persistent_mock_mounts.append((source.parent.resolve(), target_directory))

if dry_run:
    print(f"Image to build/run: {image}")
    print(f"Docker network: {network}")
    print(f"TimescaleDB host: {container_env['POSTGRES_HOST']}")
    print(f"Home Assistant URL: {container_env.get('BASE_HA_URL', '<set BASE_HA_URL>')}")
    if token_file:
        print("Home Assistant token: read-only secret file")
    else:
        print(f"SUPERVISOR_TOKEN: {'set' if os.environ.get('SUPERVISOR_TOKEN') else '<not set>'}")
    print(f"Control mode: {control_mode}")
    if control_mode == "live":
        print(f"Control allowlist: {control_allowlist}")
    print(f"HEMS data source: mock")
    if persistent:
        print(f"Persistent container: {name} (restart unless-stopped)")
        print("Mock files are mounted from their source directories (read-only).")
    else:
        print("Mock files are copied to a temporary share for this foreground run.")
    print("Mock file mounts:")
    for index, (variable, (source, relative_destination)) in enumerate(mock_sources.items()):
        if persistent:
            destination = Path("/share/local-mocks") / str(index) / source.name
        else:
            destination = Path("/share") / relative_destination
        print(f"  {source} -> {destination} ({variable})")
    print("Run logs mount: controller/logs -> /share/controller/logs")
    raise SystemExit(0)

subprocess.run(["docker", "build", "-t", image, str(script_dir)], check=True)

network_result = subprocess.run(
    ["docker", "network", "inspect", network],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
if network_result.returncode != 0:
    subprocess.run(["docker", "network", "create", network], check=True)

try:
    db_inspect = json.loads(
        subprocess.check_output(["docker", "inspect", db_container], text=True)
    )[0]
except subprocess.CalledProcessError as error:
    raise SystemExit(
        f"TimescaleDB container {db_container!r} was not found. Start it first."
    ) from error
if not db_inspect["State"]["Running"]:
    raise SystemExit(f"TimescaleDB container {db_container!r} is not running.")
if network not in db_inspect["NetworkSettings"]["Networks"]:
    subprocess.run(
        ["docker", "network", "connect", network, db_container],
        check=True,
    )

logs_dir = script_dir / "logs"
logs_dir.mkdir(parents=True, exist_ok=True)

docker_command = ["docker", "run"]
if persistent:
    docker_command.extend(["--detach", "--restart", "unless-stopped"])
else:
    docker_command.append("--rm")
docker_command.extend(
    [
        "--name",
        name,
        "--network",
        network,
        "--log-driver",
        "json-file",
        "--log-opt",
        "max-size=10m",
        "--log-opt",
        "max-file=3",
    ]
)
for variable, value in sorted(container_env.items()):
    docker_command.extend(["--env", f"{variable}={value}"])

def append_application_command() -> None:
    if token_file:
        docker_command.extend(
            [
                "--mount",
                f"type=bind,source={token_file},target=/run/secrets/ha_token,readonly",
                "--entrypoint",
                "/bin/sh",
                image,
                "-ec",
                'SUPERVISOR_TOKEN="$(cat /run/secrets/ha_token)"; export SUPERVISOR_TOKEN; exec poetry run python -m controller.main',
            ]
        )
    else:
        docker_command.extend(
            [
                "--entrypoint",
                "poetry",
                "--env",
                "SUPERVISOR_TOKEN",
                image,
                "run",
                "python",
                "-m",
                "controller.main",
            ]
        )

docker_command.extend(
    [
        "--volume",
        f"{logs_dir}:/share/controller/logs",
    ]
)

if persistent:
    for source_directory, target_directory in persistent_mock_mounts:
        docker_command.extend(
            [
                "--mount",
                f"type=bind,source={source_directory},target={target_directory},readonly",
            ]
        )
else:
    with tempfile.TemporaryDirectory(prefix="ha-hems-local-share-") as temporary_directory:
        staged_share = Path(temporary_directory) / "share"
        (staged_share / "controller" / "logs").mkdir(parents=True, exist_ok=True)
        for source, relative_destination in mock_sources.values():
            destination = staged_share / relative_destination
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        docker_command.extend(
            [
                "--volume",
                f"{staged_share}:/share:ro",
            ]
        )
        append_application_command()
        subprocess.run(docker_command, check=True)
if persistent:
    append_application_command()
    subprocess.run(docker_command, check=True)
PY
