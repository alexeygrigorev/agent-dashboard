#!/usr/bin/env bash
set -euo pipefail

# Verify ordinary Git backup and restore from the private remote repository.
REMOTE_URL="git@github.com:alexeygrigorev/agent-dashboard.git"

DISPOSABLE_DIR=$(mktemp -d -t agent-dashboard-restore-XXXXXX)
trap 'rm -rf "$DISPOSABLE_DIR"' EXIT

echo "Testing restore to disposable checkout from remote: $REMOTE_URL"
git clone --depth 1 "$REMOTE_URL" "$DISPOSABLE_DIR"

cd "$DISPOSABLE_DIR"
PYTHONPATH=src python3 -m unittest discover -s tests

echo "Remote restore verification passed successfully."
