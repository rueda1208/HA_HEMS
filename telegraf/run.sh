#!/usr/bin/with-contenv bashio

HEMS_API_BASE_URL="$(bashio::config 'hems_api_base_url')"
export HEMS_API_BASE_URL

BUILDING_ID="$(bashio::config 'building_id')"
export BUILDING_ID

CONF="$(bashio::config 'telegraf_config_path')"

bashio::log.info "Starting the custom Telegraf add-on with config ${CONF}"
telegraf --config "$CONF" --watch-config poll

