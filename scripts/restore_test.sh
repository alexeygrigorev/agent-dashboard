#!/usr/bin/env bash
set -euo pipefail

# Verify ordinary Git backup and restore in a disposable temporary directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

DISPOSABLE_DIR=$(mktemp -d -t agent-dashboard-restore-XXXXXX)
trap 'rm -rf "$DISPOSABLE_DIR"' EXIT

echo "Testing restore to disposable checkout: $DISPOSABLE_DIR"
git clone "$REPO_ROOT" "$DISPOSABLE_DIR"

cd "$DISPOSABLE_DIR"
PYTHONPATH=src python3 -m unittest discover -s tests

echo "Restore verification passed successfully."
