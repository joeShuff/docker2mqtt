# Building

Make sure Docker is running and you have tested the build locally.

1. `docker build --tag docker2mqtt .`
2. `docker image tag docker2mqtt ghcr.io/denizenn/docker2mqtt:version`
3. `docker push ghcr.io/denizenn/docker2mqtt:version`

GitHub Actions automatically builds and pushes `ghcr.io/denizenn/docker2mqtt:edge` on every push to `main`.

Published GitHub releases are pushed as both `latest` and the release tag.
