#!/usr/bin/env bash
# Builds go2rtc v1.9.15 with the Roborock MQTT client id fix (go2rtc issue #2207, PR #2346).
# Usage: go2rtc/build.sh OUT_FILE [GOARCH]  (GOARCH: amd64 default, arm64, arm). Needs git and docker.
set -euo pipefail
out=${1:?usage: go2rtc/build.sh OUT_FILE [GOARCH]}
[[ -d "$(dirname "$out")" && ! -d "$out" ]] || { echo "OUT_FILE must be a file path in an existing directory" >&2; exit 2; }
arch=${2:-amd64}
here=$(cd "$(dirname "$0")" && pwd)
tag=v1.9.15
commit=7c05066c90ab6ad1fccb506d5f0408aa31ebe60f
src=$(mktemp -d)
trap 'rm -rf "$src"' EXIT
git clone -q --depth 1 --branch "$tag" https://github.com/AlexxIT/go2rtc "$src"
test "$(git -C "$src" rev-parse HEAD)" = "$commit"
git -C "$src" apply "$here/roborock-client-id.patch"
docker run --rm -v "$src":/src -w /src -e CGO_ENABLED=0 -e GOOS=linux -e GOARCH="$arch" golang:1.26.11 \
  sh -c 'git config --global --add safe.directory /src && go build -ldflags "-s -w" -trimpath -o /src/go2rtc'
cp "$src/go2rtc" "$out"
shasum -a 256 "$out"
