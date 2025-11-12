# If => error: multiple platforms feature is currently not supported for docker driver. Please switch to a different driver (eg. "docker buildx create --use")
docker run --privileged --rm tonistiigi/binfmt --install all
docker buildx create --name multiarch-builder --use
docker buildx inspect --bootstrap
