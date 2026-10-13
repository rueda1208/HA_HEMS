# Home Assistant Add-on: Telegraf add-on

## What is does

This add-on sends metrics to:
- A local TimescaleDB instance (TimescaleDB add-on needs to be installed)
- Remote HEMS API server using HTTP POST messages

## Local development

To run Telegraf locally alongside the local controller and TimescaleDB, use `./telegraf/local_run.sh`.
It prompts for the Home Assistant URL and token, reads climate states, and writes only to the local
TimescaleDB; it does not send metrics to the HEMS API. Use `./telegraf/local_run.sh --dry-run` to
check the image, network, and database container settings without starting Telegraf.

For a persistent local container that Docker restarts after host startup, set the Home Assistant URL
and the path to a token file, then start with `--persistent`:

```bash
export BASE_HA_URL="http://hems201.hydroquebec.lab:8123"
export HA_TOKEN_FILE="/mnt/c/Users/user/ha_long_live.txt"
./telegraf/local_run.sh --persistent
```

Use the token file path as visible to the shell and Docker daemon. For Windows-mounted files, WSL
usually exposes `C:\Users\user\ha_long_live.txt` as `/mnt/c/Users/user/ha_long_live.txt`; adjust
that path if this workspace uses a different mount. The file should contain only the raw Home
Assistant long-lived token, not a `Bearer` prefix. The launcher bind-mounts it read-only into the
container and reads it there, so the token is not placed in the Docker environment. Keep the file
outside version control and restrict access to it.

The base URL is ordinary configuration, not a secret. It must be reachable from inside the
container; `localhost` would refer to the Telegraf container itself. The persistent container uses
the local config and the default TimescaleDB connection settings from `local_run.sh`. It is named
`ha-hems-telegraf-local`, uses Docker's `unless-stopped` restart policy, and can be monitored with
`docker logs -f ha-hems-telegraf-local`. Stop it with `docker stop ha-hems-telegraf-local`.
The existing Home Assistant add-on configuration is not changed by this local mode.
