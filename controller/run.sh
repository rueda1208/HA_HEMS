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

bashio::log.info "Starting the controller add-on"
poetry run python -m controller.main
