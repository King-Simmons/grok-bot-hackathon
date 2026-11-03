#!/usr/bin/env bash
# Start the control room (dashboard + URL-to-Lofi API) on :53343, and optionally the MCP HTTP server on :53350.
# First run creates ./.venv and installs requirements.txt (macOS or Linux, Python 3.10+).
# ./run.sh --voice uses a separate Python 3.11 venv (.venv-voice) with torch and Chatterbox.
set -euo pipefail
cd "$(dirname "$0")"

VOICE=0
MCP=0
for arg in "$@"; do
  case "$arg" in
    --voice) VOICE=1 ;;
    --mcp) MCP=1 ;;
    *) echo "Unknown option: $arg" >&2; echo "Usage: ./run.sh [--voice] [--mcp]" >&2; exit 1 ;;
  esac
done

pick_voice_device() {
  if [ -n "${CHATTERBOX_DEVICE:-}" ]; then
    return 0
  fi
  if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
    export CHATTERBOX_DEVICE=mps
  else
    export CHATTERBOX_DEVICE=cpu
  fi
}

if [ "$VOICE" = 1 ]; then
  if ! command -v python3.11 >/dev/null 2>&1; then
    echo "python3.11 was not found. The voice venv needs Python 3.11 because torch does not support the main venv (it may be 3.14)." >&2
    echo "Install Python 3.11, then run ./run.sh --voice again. The instrumental server is still ./run.sh." >&2
    exit 1
  fi
  pick_voice_device
  if [ -x .venv-voice/bin/python ]; then
    ver="$(.venv-voice/bin/python -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    if [ "$ver" != "3.11" ]; then
      echo ".venv-voice is Python $ver, not 3.11. Remove .venv-voice and run ./run.sh --voice again." >&2
      exit 1
    fi
  else
    python3.11 -m venv .venv-voice
    .venv-voice/bin/pip install -q --upgrade pip
    if [ "$CHATTERBOX_DEVICE" = "cpu" ]; then
      .venv-voice/bin/pip install -q torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cpu
    else
      .venv-voice/bin/pip install -q torch==2.6.0 torchaudio==2.6.0
    fi
    .venv-voice/bin/pip install -q -r scripts/requirements-voice.txt
    .venv-voice/bin/pip install -q -r requirements.txt
  fi
  PY=.venv-voice/bin/python
  echo "Voice venv: $PY (CHATTERBOX_DEVICE=$CHATTERBOX_DEVICE). First Chatterbox load takes several minutes. A warm 4-line summary is about 30s on an M1 Max."
elif [ -z "${PY:-}" ]; then
  if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
    .venv/bin/pip install -q --upgrade pip
    .venv/bin/pip install -q -r requirements.txt
  fi
  PY=.venv/bin/python
fi

export SSL_CERT_FILE="${SSL_CERT_FILE:-$("$PY" -c 'import certifi;print(certifi.where())' 2>/dev/null || true)}"
python3 dashboard/botstatus.py refresh >/dev/null 2>&1 || true
if [ "$MCP" = 1 ]; then
  "$PY" mcp/lofi_mcp_server.py --http --port 53350 > /tmp/lofi-mcp-http.log 2>&1 &
  echo "MCP (streamable HTTP) on http://127.0.0.1:53350/mcp"
fi
echo "Open http://127.0.0.1:${PORT:-53343}/dashboard/"
exec "$PY" engine/server.py --port "${PORT:-53343}"
