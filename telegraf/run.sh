#!/bin/sh
set -e

CONF=${CONF:-/etc/telegraf/telegraf.conf}

echo "Starting Telegraf with config: $CONF"
exec telegraf --config "$CONF" --watch-config poll
