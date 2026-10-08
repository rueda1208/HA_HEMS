#!/usr/bin/with-contenv bashio

HEMS_API_BASE_URL="$(bashio::config 'hems_api_base_url')"
export HEMS_API_BASE_URL

BUILDING_ID="$(bashio::config 'building_id')"
export BUILDING_ID

ENVIRONMENT_SENSOR_ID="$(bashio::config 'environment_sensor_id')"
export ENVIRONMENT_SENSOR_ID

WEATHER_ENTITY_ID="$(bashio::config 'weather_entity_id')"
export WEATHER_ENTITY_ID

HEAT_PUMP_MODEL="$(bashio::config 'heat_pump_model')"
export HEAT_PUMP_MODEL

CONTROL_MODE="$(bashio::config 'control_mode')"
CONTROL_MODE="${CONTROL_MODE:-shadow}"
export CONTROL_MODE

CONTROL_ALLOWLIST="$(bashio::config 'control_allowlist')"
CONTROL_ALLOWLIST="${CONTROL_ALLOWLIST:-}"
export CONTROL_ALLOWLIST

HEMS_POLL_SECONDS="$(bashio::config 'hems_poll_seconds')"
HEMS_POLL_SECONDS="${HEMS_POLL_SECONDS:-30}"
export HEMS_POLL_SECONDS

HA_RECONCILIATION_SECONDS="$(bashio::config 'ha_reconciliation_seconds')"
HA_RECONCILIATION_SECONDS="${HA_RECONCILIATION_SECONDS:-900}"
export HA_RECONCILIATION_SECONDS

HEMS_MAX_SNAPSHOT_AGE_SECONDS="$(bashio::config 'hems_max_snapshot_age_seconds')"
HEMS_MAX_SNAPSHOT_AGE_SECONDS="${HEMS_MAX_SNAPSHOT_AGE_SECONDS:-300}"
export HEMS_MAX_SNAPSHOT_AGE_SECONDS

bashio::log.info "Starting the controller add-on"
poetry run python -m controller.main
