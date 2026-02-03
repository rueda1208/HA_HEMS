#!/bin/bash
set -e  # Exit immediately if a command fails

# Get the directory of this script
PROJECT_ROOT="$(dirname "$(realpath "$0")")"

echo "Project root: $PROJECT_ROOT"

# Move to the project root directory
cd "$PROJECT_ROOT"

# Authenticate with Docker Hub if not already logged in
# docker login

# Check for build type argument
# Usage:
#   ./build.sh normal   → build for current platform
#   ./build.sh multi    → build multi-architecture and push
BUILD_TYPE="${1:-normal}"

IMAGE_NAME="rueda1208/controller"
VERSION="1.1.3"

if [ "$BUILD_TYPE" = "multi" ]; then
  echo "Building and pushing multi-architecture image..."
  docker buildx build \
    --platform linux/amd64,linux/arm64 \
    -f Dockerfile \
    -t ${IMAGE_NAME}:${VERSION} \
    -t ${IMAGE_NAME}:latest \
    --no-cache \
    --push .
else
  echo "Building normal image for current platform..."
  docker build \
    -f Dockerfile \
    -t ${IMAGE_NAME}:${VERSION} \
    -t ${IMAGE_NAME}:latest \
    --no-cache \
    --push .
fi

echo "Build complete!"
