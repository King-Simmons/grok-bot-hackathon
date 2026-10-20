#!/usr/bin/env bash
# Start the control room (dashboard + URL-to-Lofi API) on :53343, and optionally the MCP HTTP server on :53350.
# First run creates ./.venv and installs requirements.txt (macOS or Linux, Python 3.10+).
set -e
cd "$(dirname "$0")"
if [ -z "$PY" ]; then
  if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
    .venv/bin/pip install -q --upgrade pip
    .venv/bin/pip install -q -r requirements.txt
  fi
  PY=.venv/bin/python
fi
export SSL_CERT_FILE="${SSL_CERT_FILE:-$("$PY" -c 'import certifi;print(certifi.where())' 2>/dev/null || true)}"
python3 dashboard/botstatus.py refresh >/dev/null 2>&1 || true
if [ "$1" = "--mcp" ]; then
  "$PY" mcp/lofi_mcp_server.py --http --port 53350 > /tmp/lofi-mcp-http.log 2>&1 &
  echo "MCP (streamable HTTP) on http://127.0.0.1:53350/mcp"
fi
echo "Open http://127.0.0.1:${PORT:-53343}/dashboard/"
exec "$PY" engine/server.py --port "${PORT:-53343}"
