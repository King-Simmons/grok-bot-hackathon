#!/usr/bin/env bash
# Offline suite for Lofi News Radio. One command, no network.
#
# Add a tests/test_*.py file and this script picks it up.
# tests/server_case.py starts an in-process engine server for route tests.
# engine/test_*.py and scripts/simulate_live_agents.py stay where they are.
# scripts/smoke_topic.py and mcp/test_mcp_client.py call the network. They are not part of this run.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -n "${PY:-}" ]; then
  :
elif [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
else
  PY=python3
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export LOFI_TEST_TMP="$tmp"
export LOFI_STATUS_DIR="$tmp/status"
export LOFI_DATA_DIR="$tmp/data"
mkdir -p "$LOFI_STATUS_DIR" "$LOFI_DATA_DIR"
unset X_BEARER_TOKEN

echo "python: $PY"
"$PY" -m unittest discover -s tests -v
"$PY" -m unittest engine.test_range engine.test_variety engine.test_vocals -v
"$PY" scripts/simulate_live_agents.py
