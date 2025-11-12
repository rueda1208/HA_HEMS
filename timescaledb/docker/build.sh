#!/bin/bash
set -e  # Exit immediately if a command fails

# --- Setup ---
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
PROJECT_ROOT="$(realpath "$SCRIPT_DIR/..")"

echo "Script directory: $SCRIPT_DIR"
echo "Project root: $PROJECT_ROOT"

cd "$PROJECT_ROOT"

IMAGE_NAME="rueda1208/timescaledb"
VERSION="1.0.0"

# --- Parse arguments ---
# Usage examples:
#   ./build.sh                   → normal build (with cache)
#   ./build.sh normal --no-cache → normal build without cache
#   ./build.sh multi             → multi-arch build (with cache)
#   ./build.sh multi --no-cache  → multi-arch build without cache

BUILD_TYPE="${1:-normal}"
NO_CACHE_FLAG=""

if [[ "$2" == "--no-cache" ]]; then
  echo "No-cache mode enabled"
  NO_CACHE_FLAG="--no-cache"
fi

# --- Build ---
if [ "$BUILD_TYPE" = "multi" ]; then
  echo "Building and pushing multi-architecture image..."
  docker buildx build \
    --platform linux/amd64,linux/arm64 \
    -f docker/Dockerfile \
    -t ${IMAGE_NAME}:${VERSION} \
    -t ${IMAGE_NAME}:latest \
    ${NO_CACHE_FLAG} \
    --push .
else
  echo "Building normal image for current platform..."
  docker build \
    -f docker/Dockerfile \
    -t ${IMAGE_NAME}:${VERSION} \
    -t ${IMAGE_NAME}:latest \
    ${NO_CACHE_FLAG} .
fi

echo "Build complete!"
