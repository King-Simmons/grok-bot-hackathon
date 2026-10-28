#!/usr/bin/env bash
# Isolated Lofi News Radio session for verification.
#   session.sh launch
#   session.sh doctor <evidence-dir>
#   session.sh capture-control-room <evidence-dir>
#   session.sh cleanup <evidence-dir>
# Cleanup stops the server this launch started and deletes scratch status/data.
# Proof files under <evidence-dir>/proof and server.log stay.
set -euo pipefail

SOURCE="${BASH_SOURCE[0]}"
while [[ -L "$SOURCE" ]]; do
  DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

die() { echo "session.sh: $*" >&2; exit 1; }

chrome_bin() {
  local c
  # Prefer the real binary. A PATH wrapper that pins a shared user-data-dir
  # makes headless --dump-dom wait forever.
  for c in /opt/google/chrome/chrome /usr/bin/google-chrome-stable /usr/bin/chromium /usr/bin/chromium-browser; do
    if [[ -x "$c" && ! -L "$c" ]]; then
      echo "$c"
      return 0
    fi
  done
  for c in google-chrome google-chrome-stable chromium chromium-browser; do
    if command -v "$c" >/dev/null 2>&1; then
      command -v "$c"
      return 0
    fi
  done
  return 1
}

load_env() {
  local evidence="$1"
  [[ -f "$evidence/session.env" ]] || die "no session.env in $evidence"
  # shellcheck disable=SC1090
  set -a
  source "$evidence/session.env"
  set +a
  [[ -n "${PORT:-}" && -n "${PID:-}" && -n "${BASE:-}" ]] || die "session.env is incomplete"
}

cmd_launch() {
  local run_id evidence port
  run_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
  evidence="${LOFI_VERIFY_EVIDENCE:-/tmp/lofi-news-radio-verify/$run_id}"
  mkdir -p "$evidence/proof" "$evidence/status" "$evidence/data"
  port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
  (
    cd "$ROOT"
    export PORT="$port"
    export LOFI_STATUS_DIR="$evidence/status"
    export LOFI_DATA_DIR="$evidence/data"
    exec ./run.sh
  ) >"$evidence/server.log" 2>&1 &
  local pid=$!
  cat >"$evidence/session.env" <<EOF
EVIDENCE=$evidence
PORT=$port
BASE=http://127.0.0.1:$port
PID=$pid
LOFI_STATUS_DIR=$evidence/status
LOFI_DATA_DIR=$evidence/data
ROOT=$ROOT
EOF
  echo "$evidence"
}

cmd_doctor() {
  local evidence="$1"
  load_env "$evidence"
  if [[ ! -d "/proc/$PID" ]]; then
    die "server pid $PID is not running. See $evidence/server.log"
  fi
  local cmd
  cmd="$(tr '\0' ' ' <"/proc/$PID/cmdline" || true)"
  case "$cmd" in
    *engine/server.py*|*run.sh*) ;;
    *) die "pid $PID is not the server we started ($cmd)" ;;
  esac
  local i
  # First ./run.sh creates .venv and installs requirements.txt before it listens.
  for i in $(seq 1 300); do
    if python3 - "$BASE" <<'PY'
import json, sys, urllib.request
url = sys.argv[1] + "/api/health"
try:
    with urllib.request.urlopen(url, timeout=2) as resp:
        body = json.load(resp)
except Exception:
    sys.exit(1)
sys.exit(0 if body.get("ok") is True and "jobs" in body else 1)
PY
    then
      echo "doctor ok $BASE pid $PID"
      return 0
    fi
    if [[ ! -d "/proc/$PID" ]]; then
      die "server pid $PID exited before /api/health. See $evidence/server.log"
    fi
    sleep 1
  done
  die "timed out waiting for $BASE/api/health. See $evidence/server.log"
}

cmd_capture() {
  local evidence="$1"
  load_env "$evidence"
  local proof="$evidence/proof"
  mkdir -p "$proof"
  python3 - "$BASE" "$proof" <<'PY'
import json, sys, urllib.request
base, proof = sys.argv[1], sys.argv[2]

def get(path):
    with urllib.request.urlopen(base + path, timeout=20) as resp:
        raw = resp.read()
        return resp.status, resp.headers.get("Content-Type", ""), raw

checks = []
status, ctype, raw = get("/api/health")
body = json.loads(raw)
open(proof + "/health.json", "w").write(json.dumps(body, indent=2) + "\n")
checks.append(status == 200 and body.get("ok") is True)

status, ctype, raw = get("/dashboard/")
text = raw.decode("utf-8", "replace")
open(proof + "/dashboard.html", "w").write(text)
needles = [
    "<title>Lofi News Radio · Dashboard</title>",
    "Lofi News Radio",
    'id="tabRadio"',
    'id="tabGraph"',
    'id="u2lUrl"',
    'id="xTopic"',
    'id="botList"',
    'id="playBtn"',
    'id="rDial"',
    'id="usage"',
]
missing = [n for n in needles if n not in text]
open(proof + "/dashboard.needles.txt", "w").write("\n".join(missing) + ("\n" if missing else ""))
checks.append(status == 200 and not missing)

status, ctype, raw = get("/api/agents")
agents = json.loads(raw)
open(proof + "/agents.json", "w").write(json.dumps(agents, indent=2) + "\n")
checks.append(status == 200 and "bots" in agents and "protocol" in agents)

status, ctype, raw = get("/api/usage")
usage = json.loads(raw)
open(proof + "/usage.json", "w").write(json.dumps(usage, indent=2) + "\n")
checks.append(status == 200 and "today" in usage and "all_time" in usage)

status, ctype, raw = get("/api/topics")
topics = json.loads(raw)
open(proof + "/topics.json", "w").write(json.dumps(topics, indent=2) + "\n")
checks.append(status == 200 and topics.get("default_source") == "web" and "topics" in topics)

status, ctype, raw = get("/dashboard/headlines.json")
headlines = json.loads(raw)
open(proof + "/headlines.json", "w").write(json.dumps({"source": headlines.get("source"), "count": len(headlines.get("headlines") or [])}, indent=2) + "\n")
checks.append(status == 200 and headlines.get("source") == "Hacker News" and len(headlines.get("headlines") or []) > 0)

status, ctype, raw = get("/dashboard/episode.json")
episode = json.loads(raw)
open(proof + "/episode.json", "w").write(json.dumps({"podcast_audio": episode.get("podcast_audio"), "file": episode.get("file")}, indent=2) + "\n")
checks.append(status == 200 and episode.get("podcast_audio") == "host_briefing.mp3")

status, ctype, raw = get("/samples/beat-001.mp3")
open(proof + "/beat-001.size.txt", "w").write(str(len(raw)) + "\n")
checks.append(status == 200 and len(raw) > 1000)

if not all(checks):
    sys.exit("control-room HTTP checks failed")
print("http checks ok")
PY

  local chrome=""
  if chrome="$(chrome_bin)"; then
    local profile="$evidence/chrome-profile"
    mkdir -p "$profile"
    # ?hn=0 keeps the page on the committed headlines snapshot.
    local room="$BASE/dashboard/?hn=0"
    local graph="$BASE/dashboard/?hn=0#graph"
    local common=(
      --headless=new --disable-gpu --no-sandbox --disable-dev-shm-usage
      --no-first-run --no-default-browser-check
      --user-data-dir="$profile"
      --virtual-time-budget=8000
    )
    timeout 30 "$chrome" "${common[@]}" --dump-dom "$room" >"$proof/dashboard.dom.html" 2>"$proof/chrome-dom.stderr"
    timeout 30 "$chrome" "${common[@]}" --window-size=1280,900 --screenshot="$proof/dashboard.png" "$room" >"$proof/chrome-shot.stdout" 2>"$proof/chrome-shot.stderr"
    timeout 30 "$chrome" "${common[@]}" --dump-dom "$graph" >"$proof/graph.dom.html" 2>"$proof/chrome-graph.stderr"
    python3 - "$proof" <<'PY'
import sys
proof = sys.argv[1]
dom = open(proof + "/dashboard.dom.html", encoding="utf-8", errors="replace").read()
graph = open(proof + "/graph.dom.html", encoding="utf-8", errors="replace").read()
need = ['data-st="lofi"', 'data-st="talk"', 'id="rFreq"', "Lofi News Radio"]
missing = [n for n in need if n not in dom]

def opening_tag(html, element_id):
    token = 'id="%s"' % element_id
    i = html.find(token)
    if i < 0:
        return ""
    start = html.rfind("<", 0, i)
    end = html.find(">", i)
    if start < 0 or end < 0:
        return ""
    return html[start:end + 1]

tag = opening_tag(graph, "viewGraph")
graph_ok = bool(tag) and "hidden" not in tag and 'id="gSvg"' in graph
open(proof + "/dom.needles.txt", "w").write(
    ("\n".join(missing) + ("\n" if missing else "")) + "viewGraph: %s\n" % (tag or "MISSING")
)
if missing or not graph_ok:
    sys.exit("dashboard DOM checks failed: missing=%s graph_ok=%s tag=%s" % (missing, graph_ok, tag))
print("dom checks ok")
PY
  else
    echo "no Chrome or Chromium on PATH; skipped DOM and screenshot" | tee "$proof/browser-skipped.txt"
  fi
  echo "captured $proof"
}

cmd_cleanup() {
  local evidence="$1"
  load_env "$evidence"
  if [[ -d "/proc/$PID" ]]; then
    local cmd
    cmd="$(tr '\0' ' ' <"/proc/$PID/cmdline" || true)"
    case "$cmd" in
      *engine/server.py*|*run.sh*)
        kill "$PID" 2>/dev/null || true
        local i
        for i in $(seq 1 20); do
          [[ -d "/proc/$PID" ]] || break
          sleep 0.2
        done
        if [[ -d "/proc/$PID" ]]; then
          kill -9 "$PID" 2>/dev/null || true
        fi
        ;;
      *) die "refusing to kill pid $PID ($cmd)" ;;
    esac
  fi
  rm -rf "$evidence/status" "$evidence/data"
  if [[ ! -f "$evidence/proof/health.json" ]]; then
    die "cleanup removed scratch, but proof/health.json is missing"
  fi
  echo "cleaned $evidence (proof kept)"
}

usage() {
  echo "usage: session.sh launch | doctor <evidence> | capture-control-room <evidence> | cleanup <evidence>" >&2
  exit 2
}

main() {
  local cmd="${1:-}"
  case "$cmd" in
    launch) cmd_launch ;;
    doctor) [[ $# -eq 2 ]] || usage; cmd_doctor "$2" ;;
    capture-control-room) [[ $# -eq 2 ]] || usage; cmd_capture "$2" ;;
    cleanup) [[ $# -eq 2 ]] || usage; cmd_cleanup "$2" ;;
    *) usage ;;
  esac
}

main "$@"
