#!/usr/bin/with-contenv bashio

HEMS_API_BASE_URL="$(bashio::config 'hems_api_base_url')"
export HEMS_API_BASE_URL

BUILDING_ID="$(bashio::config 'building_id')"
export BUILDING_ID

CONF="$(bashio::config 'telegraf_config_path')"

# DB connection params (same as telegraf.conf)
PGHOST="77b2833f-timescaledb"
PGUSER="postgres"
PGPASSWORD="homeassistant"
PGDATABASE="homeassistant"
export PGHOST PGUSER PGPASSWORD PGDATABASE

bashio::log.info "Initializing TimescaleDB schema (public.space_heating)…"
psql <<'SQL'
CREATE TABLE IF NOT EXISTS public.space_heating (
  time        TIMESTAMPTZ NOT NULL,
  device_id   TEXT,
  metric_type TEXT,
  name        TEXT,
  value       DOUBLE PRECISION
);

-- If TimescaleDB is present, this will do nothing if the hypertable already exists
SELECT create_hypertable('public.space_heating', 'time', if_not_exists => TRUE);
SQL

bashio::log.info "Starting the custom Telegraf add-on with config ${CONF}"
telegraf --config "$CONF" --watch-config poll

