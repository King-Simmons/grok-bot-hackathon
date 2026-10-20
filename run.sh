#!/usr/bin/env bash
# Serve the dashboard and the URL-to-Lofi API from the repository root.
# First run creates ./.venv and installs requirements.txt.
#   ./run.sh                 dashboard + API
#   ./run.sh --port 53343    extra args go to engine/server.py
#   ./run.sh --mcp           also start mcp/lofi_mcp_server.py
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi

export SSL_CERT_FILE="$(.venv/bin/python -c 'import certifi; print(certifi.where())')"

mcp=0
args=()
for arg in "$@"; do
  if [[ "$arg" == "--mcp" ]]; then
    mcp=1
  else
    args+=("$arg")
  fi
done

if [[ "$mcp" == 1 ]]; then
  if [[ ! -f mcp/lofi_mcp_server.py ]]; then
    echo "mcp/lofi_mcp_server.py is not in this checkout" >&2
    exit 1
  fi
  .venv/bin/python mcp/lofi_mcp_server.py &
  mcp_pid=$!
  trap 'kill "$mcp_pid" 2>/dev/null || true' EXIT
fi

exec .venv/bin/python engine/server.py "${args[@]}"
