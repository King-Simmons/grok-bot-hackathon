#!/usr/bin/env python3
"""Simulate agents appearing, updating, and going offline, and check the usage meter.

Runs an in-process engine server against temp status/ and data/ directories, so the
repo's real status files are left alone. Stdlib plus the engine modules.

    python3 scripts/simulate_live_agents.py
"""
import datetime, importlib.util, json, os, subprocess, sys, tempfile, threading, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TMP = tempfile.TemporaryDirectory()
os.environ["LOFI_STATUS_DIR"] = str(Path(_TMP.name) / "status")
os.environ["LOFI_DATA_DIR"] = str(Path(_TMP.name) / "data")
os.environ["LOFI_USAGE_MAX_BYTES"] = "1200"
os.environ["LOFI_USAGE_MAX_EVENTS"] = "40"
os.environ.pop("LOFI_AGENT_STALE_S", None)
Path(os.environ["LOFI_STATUS_DIR"]).mkdir()
Path(os.environ["LOFI_DATA_DIR"]).mkdir()

from http.server import ThreadingHTTPServer  # noqa: E402
from engine import agents, server, usage  # noqa: E402


def fail(msg):
    print("FAIL:", msg, file=sys.stderr)
    sys.exit(1)


def main():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def call(method, path, body=None, raw=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        req = urllib.request.Request(base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = resp.read().decode()
                return resp.status, json.loads(payload) if payload else {}
        except urllib.error.HTTPError as e:
            payload = e.read().decode()
            try:
                parsed = json.loads(payload)
            except Exception:
                parsed = {"raw": payload}
            return e.code, parsed

    # A static sample must not show up as a live agent.
    sample = Path(os.environ["LOFI_STATUS_DIR"]) / "example-zlatan.json"
    sample.write_text(json.dumps({
        "id": "example-zlatan", "name": "Zlatan", "owner": "King", "state": "running",
        "task": "static sample", "last_update": "2020-01-01T00:00:00-06:00", "example": True,
    }))

    code, rec = call("POST", "/api/agents", {"id": "alpha-news", "name": "Alpha", "owner": "King",
                                              "role": "headlines", "state": "running", "task": "Pulling headlines", "progress": 10})
    if code != 200 or rec.get("presence") != "online":
        fail(f"alpha check-in {code} {rec}")
    code, rec = call("POST", "/api/agents", {"id": "beta-beats", "name": "Beta", "owner": "Ada",
                                              "state": "running", "task": "Rendering a beat"})
    if code != 200:
        fail(f"beta check-in {code} {rec}")
    code, rec = call("POST", "/api/agents", {"id": "gamma-voice", "name": "Gamma", "owner": "Jin",
                                              "state": "idle", "task": "Waiting on a script"})
    if code != 200:
        fail(f"gamma check-in {code} {rec}")

    code, listing = call("GET", "/api/agents")
    ids = [b["id"] for b in listing.get("bots", [])]
    if code != 200 or ids.count("alpha-news") != 1 or "example-zlatan" in ids or len(ids) != 3:
        fail(f"list after appear {code} {ids}")
    if listing.get("stale_after_s") != 1800:
        fail(f"timeout {listing.get('stale_after_s')}")

    code, rec = call("POST", "/api/agents", {"id": "alpha-news", "task": "Headlines filed", "state": "done", "progress": 100})
    if code != 200 or rec.get("task") != "Headlines filed" or rec.get("owner") != "King":
        fail(f"alpha update {code} {rec}")

    # Cap: two new files would exceed a temporary cap of 3 (alpha, beta, gamma already exist).
    agents.MAX_AGENTS = 3
    code, rec = call("POST", "/api/agents", {"id": "delta-extra", "owner": "King", "state": "idle", "task": "too many"})
    if code != 409:
        fail(f"expected cap 409, got {code} {rec}")
    code, rec = call("POST", "/api/agents", {"id": "gamma-voice", "task": "Still waiting"})
    if code != 200:
        fail(f"update under cap should work {code} {rec}")
    agents.MAX_AGENTS = 48

    code, rec = call("POST", "/api/agents", {"id": "Bad ID", "owner": "King", "state": "idle", "task": "nope"})
    if code != 400:
        fail(f"bad id {code} {rec}")
    code, rec = call("POST", "/api/agents", raw=b"this is not json")
    if code != 400:
        fail(f"non-json {code} {rec}")
    code, rec = call("POST", "/api/agents", raw=b'{"id":"x"' + b" " * 9000 + b"}")
    if code != 413:
        fail(f"oversize {code} {rec}")

    # Beta stops sending updates. Rewrite last_update; the file stays, presence flips.
    beta_path = Path(os.environ["LOFI_STATUS_DIR"]) / "beta-beats.json"
    beta = json.loads(beta_path.read_text())
    old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=agents.botstatus.STALE_AFTER_S + 5)
    beta["last_update"] = old.astimezone().isoformat(timespec="seconds")
    beta_path.write_text(json.dumps(beta))
    code, listing = call("GET", "/api/agents")
    by_id = {b["id"]: b for b in listing["bots"]}
    if by_id["beta-beats"]["presence"] != "offline":
        fail(f"beta should be offline: {by_id['beta-beats']}")
    if by_id["gamma-voice"]["presence"] != "online":
        fail(f"gamma should still be online: {by_id['gamma-voice']}")
    if by_id["alpha-news"]["presence"] != "settled":
        fail(f"done agent should stay settled: {by_id['alpha-news']}")
    if not beta_path.exists():
        fail("offline agent file was deleted")

    # Usage: check-ins already recorded report_status. Add a synth, a topic run, and X reads.
    before = usage.summary()["all_time"]["calls"]
    usage.record("dashboard", "synthesize", True, 1.5, audio_s=44.0, tracks=1)
    usage.record("anonymous", "synthesize", False, 0.2, error="blocked")
    usage.record("alpha-news", "synthesize_topic", True, 0.4, audio_s=30, tracks=1)
    usage.note_x_search("alpha-news", 4, duration_s=0.3)
    summary = usage.summary()
    if summary["all_time"]["calls"] < before + 4:
        fail(f"usage calls {summary['all_time']}")
    if summary["all_time"]["tracks"] < 2 or summary["all_time"]["errors"] < 1:
        fail(f"usage totals {summary['all_time']}")
    if summary["tools"].get("synthesize_topic", {}).get("tracks") != 1:
        fail(f"synthesize_topic {summary.get('tools')}")
    if summary["tools"].get("x_search", {}).get("x_reads") != 4:
        fail(f"x_search {summary.get('tools')}")
    if summary["x_reads_month"] != 4 or summary["x_reads_remaining"] != summary["x_free_tier_monthly_reads"] - 4:
        fail(f"x budget {summary['x_reads_month']} {summary['x_reads_remaining']}")
    if len(summary["hours"]) != 24:
        fail(f"sparkline {len(summary['hours'])}")
    names = {a["agent"] for a in summary["agents"]}
    if "dashboard" not in names or "alpha-news" not in names:
        fail(f"agents {names}")

    code, api_usage = call("GET", "/api/usage")
    if code != 200 or api_usage["all_time"]["tracks"] != summary["all_time"]["tracks"]:
        fail(f"GET /api/usage {code} {api_usage.get('all_time')}")

    # Raw log rotates under the byte cap; all-time totals do not shrink.
    usage.MAX_BYTES = 400
    calls_before_rotate = usage.summary()["all_time"]["calls"]
    for i in range(25):
        usage.record("rot", "report_status", True, 0.01)
    log_size = os.path.getsize(usage.LOG_PATH)
    if log_size > 4000:
        fail(f"log did not rotate: {log_size} bytes")
    if usage.summary()["all_time"]["calls"] < calls_before_rotate + 25:
        fail("rotation dropped all-time totals")

    # MCP tools write the same files and the same meter.
    spec = importlib.util.spec_from_file_location("lofi_mcp_server", ROOT / "mcp" / "lofi_mcp_server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import anyio

    async def mcp_round():
        rec = await mod.report_status(id="mcp-bot", name="MCP Bot", owner="King", state="running", task="Checked in via MCP")
        listed = await mod.list_agents()
        meter = await mod.get_usage()
        return rec, listed, meter

    rec, listed, meter = anyio.run(mcp_round)
    if rec.get("id") != "mcp-bot" or rec.get("presence") != "online":
        fail(f"mcp report_status {rec}")
    if "mcp-bot" not in {b["id"] for b in listed["bots"]}:
        fail("mcp list_agents missed mcp-bot")
    if meter["tools"].get("report_status", {}).get("calls", 0) < 1:
        fail("mcp get_usage missed report_status")

    # A fresh interpreter picks up LOFI_AGENT_STALE_S. Bad values stay on the default.
    probe = (
        "import importlib.util, os\n"
        "spec = importlib.util.spec_from_file_location('botstatus', os.environ['BOTSTATUS'])\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "print(m.STALE_AFTER_S)\n"
    )
    botstatus_py = str(ROOT / "dashboard" / "botstatus.py")
    for raw, expect in (("45", "45"), ("0", "1800"), ("nope", "1800")):
        env = os.environ.copy()
        env["LOFI_AGENT_STALE_S"] = raw
        env["BOTSTATUS"] = botstatus_py
        got = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True)
        if got.returncode != 0 or got.stdout.strip() != expect:
            fail(f"LOFI_AGENT_STALE_S={raw!r} -> {got.stdout!r} {got.stderr!r}, expected {expect}")

    httpd.shutdown()
    print(f"simulate_live_agents: ok (port {port}, stale_after_s={agents.botstatus.STALE_AFTER_S})")


if __name__ == "__main__":
    main()
