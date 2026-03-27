#!/bin/bash
set -e

cd "$(dirname "$0")/injector"

docker run --rm \
  -v "$PWD":/src \
  -w /src \
  golang:1.23-alpine \
  sh -c "apk add --no-cache git && GOARCH=arm64 GOOS=linux go build -o ../thirdeye-injector-arm64 -ldflags='-s -w' ."

echo "✓ Built thirdeye-injector-arm64"
