#!/usr/bin/env bash

set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_context="$(mktemp -d /private/tmp/budget-intelligence-build.XXXXXX)"

cleanup() {
  rm -rf -- "$build_context"
}
trap cleanup EXIT

rsync -a \
  --exclude '.cache' \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude 'analysis' \
  --exclude 'apps/web/dist' \
  --exclude 'apps/web/node_modules' \
  --exclude 'docs' \
  --exclude '*.docx' \
  --exclude '*.rtf' \
  --exclude '*.xlsx' \
  "$project_root/" "$build_context/"

docker build --target api -t budget-intelligence-api:local "$build_context"
docker build --target worker -t budget-intelligence-worker:local "$build_context"
docker build -t budget-intelligence-web:local "$build_context/apps/web"
