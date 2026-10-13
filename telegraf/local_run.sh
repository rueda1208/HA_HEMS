#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
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

if [[ ! -f "$SCRIPT_DIR/telegraf.local.conf" ]]; then
  echo "Local Telegraf config not found: $SCRIPT_DIR/telegraf.local.conf" >&2
  exit 1
fi

if [[ "$DRY_RUN" == true ]]; then
  echo "Image to build/run: ${LOCAL_TELEGRAF_IMAGE:-ha-hems-telegraf:local}"
  if [[ "$PERSISTENT" == true ]]; then
    echo "Run mode: detached, restart unless-stopped"
    echo "Container name: ha-hems-telegraf-local"
  else
    echo "Run mode: foreground, removed on exit"
  fi
  echo "Docker network: ${LOCAL_DOCKER_NETWORK:-hems-local}"
  echo "TimescaleDB container: ${LOCAL_TIMESCALE_CONTAINER:-ha-hems-timescaledb}"
  echo "Local config: $SCRIPT_DIR/telegraf.local.conf"
  if [[ -n "${HA_TOKEN_FILE:-}" ]]; then
    echo "HA token: read-only secret file (path configured)"
  else
    echo "HA token: secure interactive prompt"
  fi
  echo "Outputs: local TimescaleDB only (no HEMS API output)"
  exit 0
fi

if [[ -z "${BASE_HA_URL:-}" ]]; then
  read -r -p "Reachable Home Assistant base URL (for example http://192.168.1.10:8123): " BASE_HA_URL
fi
BASE_HA_URL="${BASE_HA_URL%/}"
if [[ ! "$BASE_HA_URL" =~ ^https?://[^/]+$ ]]; then
  echo "BASE_HA_URL must be a valid Home Assistant base URL." >&2
  exit 1
fi
export BASE_HA_URL

token_file="${HA_TOKEN_FILE:-}"
if [[ -n "$token_file" ]]; then
  if [[ ! -f "$token_file" || ! -r "$token_file" || ! -s "$token_file" ]]; then
    echo "HA_TOKEN_FILE must point to a readable, non-empty token file." >&2
    exit 1
  fi
else
  if [[ -z "${SUPERVISOR_TOKEN:-}" ]]; then
    read -r -s -p "Home Assistant long-lived access token: " SUPERVISOR_TOKEN
    printf '\n'
  fi
  if [[ -z "${SUPERVISOR_TOKEN:-}" ]]; then
    echo "SUPERVISOR_TOKEN is required to read Home Assistant states." >&2
    exit 1
  fi
  export SUPERVISOR_TOKEN
fi

db_container="${LOCAL_TIMESCALE_CONTAINER:-ha-hems-timescaledb}"
network="${LOCAL_DOCKER_NETWORK:-hems-local}"
image="${LOCAL_TELEGRAF_IMAGE:-ha-hems-telegraf:local}"
postgres_port="${POSTGRES_PORT:-5432}"
postgres_name="${POSTGRES_NAME:-homeassistant}"
postgres_user="${POSTGRES_USER:-postgres}"
postgres_password="${POSTGRES_PASSWORD:-homeassistant}"

db_state="$(docker inspect --format '{{.State.Status}}' "$db_container" 2>/dev/null || true)"
if [[ "$db_state" != "running" ]]; then
  echo "TimescaleDB container '$db_container' must be running." >&2
  exit 1
fi

if ! docker network inspect "$network" >/dev/null 2>&1; then
  docker network create "$network" >/dev/null
fi
db_networks="$(docker inspect --format '{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}' "$db_container")"
if ! grep -Fxq "$network" <<<"$db_networks"; then
  docker network connect "$network" "$db_container"
fi

docker exec -i \
  -e "PGPASSWORD=$postgres_password" \
  "$db_container" \
  psql -v ON_ERROR_STOP=1 -U "$postgres_user" -d "$postgres_name" <<'SQL'
CREATE TABLE IF NOT EXISTS public.space_heating (
  time        TIMESTAMPTZ NOT NULL,
  device_id   TEXT,
  metric_type TEXT,
  name        TEXT,
  value       DOUBLE PRECISION
);
SELECT create_hypertable('public.space_heating', 'time', if_not_exists => TRUE);
SQL

docker build -t "$image" "$SCRIPT_DIR"

postgres_connection="host=$db_container port=$postgres_port user=$postgres_user password=$postgres_password dbname=$postgres_name sslmode=disable"
if [[ "$PERSISTENT" == true ]]; then
  container_name="ha-hems-telegraf-local"
  run_options=(--detach --restart unless-stopped)
else
  container_name="ha-hems-telegraf-local-$$"
  run_options=(--rm)
fi

docker_run=(
  docker run
  "${run_options[@]}"
  --name "$container_name" \
  --network "$network" \
  --log-driver json-file \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  --env BASE_HA_URL \
  --env "POSTGRES_CONNECTION=$postgres_connection" \
  --volume "$SCRIPT_DIR/telegraf.local.conf:/etc/telegraf/telegraf.local.conf:ro" \
)

if [[ -n "$token_file" ]]; then
  docker_run+=(
    --mount "type=bind,source=$token_file,target=/run/secrets/ha_token,readonly"
    --entrypoint /bin/sh
    "$image"
    -ec
    'SUPERVISOR_TOKEN="$(cat /run/secrets/ha_token)"; export SUPERVISOR_TOKEN; exec telegraf --config /etc/telegraf/telegraf.local.conf'
  )
else
  docker_run+=(
    --env SUPERVISOR_TOKEN
    --entrypoint telegraf
    "$image"
    --config /etc/telegraf/telegraf.local.conf
  )
fi

"${docker_run[@]}"
