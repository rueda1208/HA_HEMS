#!/usr/bin/env bash
set -e

DB_DIR="/data/postgresql"

POSTGRES_USER="${POSTGRES_USER:-homeassistant}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-secret}"
POSTGRES_DB="${POSTGRES_DB:-homeassistant}"

echo "-----------------------------------------------------------"
echo "Starting TimescaleDB..."
echo "User: ${POSTGRES_USER}, Database: ${POSTGRES_DB}"
echo "Data directory: ${DB_DIR}"
echo "-----------------------------------------------------------"

mkdir -p "${DB_DIR}"

# Initialize if first run
if [ ! -d "${DB_DIR}/base" ]; then
  echo "Initializing new database cluster..."
  initdb -D "${DB_DIR}"
  
  echo "host all all 0.0.0.0/0 md5" >> "${DB_DIR}/pg_hba.conf"
  echo "listen_addresses='*'" >> "${DB_DIR}/postgresql.conf"

  echo "Starting temporary PostgreSQL for setup..."
  pg_ctl -D "${DB_DIR}" -o "-c listen_addresses='localhost'" -w start

  psql --username=postgres <<-EOSQL
    CREATE USER ${POSTGRES_USER} WITH PASSWORD '${POSTGRES_PASSWORD}';
    CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER};
    \connect ${POSTGRES_DB}
    CREATE EXTENSION IF NOT EXISTS timescaledb;
  EOSQL

  pg_ctl -D "${DB_DIR}" -m fast -w stop
  echo "Database initialized successfully!"
fi

echo "Starting PostgreSQL..."
exec postgres -D "${DB_DIR}"
