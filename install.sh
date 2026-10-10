#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_APPS_DIR="${LOCAL_APPS_DIR:-$HOME/local_apps}"
SHARE_CONFIG_DIR="${SHARE_CONFIG_DIR:-/share/controller/config}"
DEFAULT_ADDON_NAME="Controller Shadow Test"
ADDON_NAME="${ADDON_NAME:-$DEFAULT_ADDON_NAME}"

if [[ -t 0 ]]; then
  read -r -p "Local add-on display name [$ADDON_NAME]: " ADDON_NAME_INPUT || ADDON_NAME_INPUT=""
  ADDON_NAME="${ADDON_NAME_INPUT:-$ADDON_NAME}"
fi

ADDON_NAME_PATTERN='^[A-Za-z0-9][A-Za-z0-9 ._-]*$'
if [[ ! "$ADDON_NAME" =~ $ADDON_NAME_PATTERN ]]; then
  echo "Use letters, numbers, spaces, dots, underscores, or hyphens in the add-on name." >&2
  exit 1
fi

DEFAULT_ADDON_SLUG="$(printf '%s' "$ADDON_NAME" \
  | tr '[:upper:]' '[:lower:]' \
  | sed -E 's/[^a-z0-9]+/_/g; s/^_+|_+$//g')"
ADDON_SLUG="${ADDON_SLUG:-$DEFAULT_ADDON_SLUG}"
if [[ -t 0 ]]; then
  read -r -p "Local add-on slug [$ADDON_SLUG]: " ADDON_SLUG_INPUT || ADDON_SLUG_INPUT=""
  ADDON_SLUG="${ADDON_SLUG_INPUT:-$ADDON_SLUG}"
fi

if [[ ! "$ADDON_SLUG" =~ ^[a-z0-9_]+$ ]]; then
  echo "The add-on slug must contain only lowercase letters, numbers, and underscores." >&2
  exit 1
fi

ADDON_DEST="$LOCAL_APPS_DIR/$ADDON_SLUG"

require_file() {
  if [[ ! -f "$1" ]]; then
    echo "Required repository file not found: $1" >&2
    exit 1
  fi
}

copy_example_if_missing() {
  local source="$1"
  local destination="$2"

  if [[ -e "$destination" ]]; then
    echo "Keeping existing mock file: $destination"
  else
    cp "$source" "$destination"
    echo "Installed mock file: $destination"
  fi
}

require_file "$REPO_ROOT/controller/config.yaml"
require_file "$REPO_ROOT/controller/Dockerfile"
require_file "$REPO_ROOT/config/controller/device-configuration.mock.example.json"
require_file "$REPO_ROOT/config/controller/peak-events.example.json"
require_file "$REPO_ROOT/config/controller/heat-pump.example.yaml"

if [[ -e "$ADDON_DEST" ]]; then
  if [[ ! -f "$ADDON_DEST/config.yaml" ]] || ! grep -q "^slug: \"$ADDON_SLUG\"" "$ADDON_DEST/config.yaml"; then
    echo "Destination already exists and is not this add-on: $ADDON_DEST" >&2
    exit 1
  fi
fi

mkdir -p "$ADDON_DEST" "$SHARE_CONFIG_DIR"
cp -a "$REPO_ROOT/controller/." "$ADDON_DEST/"

ADDON_CONFIG="$ADDON_DEST/config.yaml"
escape_sed_replacement() {
  printf '%s' "$1" | sed 's/[\\&|]/\\&/g'
}

ESCAPED_ADDON_NAME="$(escape_sed_replacement "$ADDON_NAME")"
ESCAPED_ADDON_SLUG="$(escape_sed_replacement "$ADDON_SLUG")"
sed -i \
  -e "s|^name: \"Controller\".*|name: \"$ESCAPED_ADDON_NAME\"|" \
  -e "s|^slug: \"controller\".*|slug: \"$ESCAPED_ADDON_SLUG\"|" \
  -e 's/^  hems_data_source: api/  hems_data_source: mock/' \
  "$ADDON_CONFIG"

if ! grep -q "^name: \"$ADDON_NAME\"" "$ADDON_CONFIG" \
  || ! grep -q "^slug: \"$ADDON_SLUG\"" "$ADDON_CONFIG"; then
  echo "Could not apply the requested add-on name and slug in $ADDON_CONFIG" >&2
  exit 1
fi

copy_example_if_missing \
  "$REPO_ROOT/config/controller/device-configuration.mock.example.json" \
  "$SHARE_CONFIG_DIR/device-configuration.json"
copy_example_if_missing \
  "$REPO_ROOT/config/controller/peak-events.example.json" \
  "$SHARE_CONFIG_DIR/peak-events.json"
copy_example_if_missing \
  "$REPO_ROOT/config/controller/heat-pump.example.yaml" \
  "$SHARE_CONFIG_DIR/heat-pump.yaml"

echo "Add-on '$ADDON_NAME' (slug: $ADDON_SLUG) prepared at: $ADDON_DEST"
echo "Shared mock files are under: $SHARE_CONFIG_DIR"
echo "Update the sample GDP event dates before testing; the checked-in dates are illustrative."
echo "In Home Assistant, reload local add-ons, install $ADDON_NAME, review its options, and start it in shadow mode."