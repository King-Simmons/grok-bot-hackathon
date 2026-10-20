"""engine/server.py - serves the dashboard (static, same paths as before) + the URL-to-Lofi API.

  GET  /api/health
  GET  /api/mood?url=...                 -> PRD mood JSON (+ analysis/timing under _meta)
  GET  /api/agents                       -> live agents (offline computed from last_update)
  GET  /api/usage                        -> local usage totals (no network)
  POST /api/agents                       -> register or update one agent
  POST /api/synthesize {"url": ...}      -> {"mood": {...PRD...}, "track_url": "/tracks/....mp3", ...}
       add "async": true                 -> {"job_id": ...}; poll GET /api/jobs/<id>
       add "force": true                 -> re-render even if this exact track exists
       optional "agent_id" (or header X-Agent-Id) is who the usage log credits
Run from the repo root: ./run.sh   or   python3 engine/server.py [--port 53343]
The process serves the repo root. Open /dashboard/ for the control room. API routes are /api/*.
Binds to 127.0.0.1 by default. No auth secrets.

Live agents and the usage meter live in the named helpers below (route_agents_*,
route_mood_get, route_synthesize_post, note_synthesize). Polls of /api/agents and
/api/usage are not recorded.
"""
import argparse, json, os, sys, threading, time, uuid, urllib.parse
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import agents, pipeline, usage  # noqa: E402
from engine.ingest import IngestError  # noqa: E402

JOBS = {}
JOBS_LOCK = threading.Lock()


def _result(r):
    return {"mood": r["mood"], "track_url": r["track"]["url"], "track": r["track"], "page": r["page"],
            "analysis": r["analysis"], "timing": r["timing"]}


def note_synthesize(agent, started, result=None, error=""):
    """Credit one synthesize call. Cached tracks do not count as newly made."""
    track = (result or {}).get("track") or {}
    timing = (result or {}).get("timing") or {}
    audio = float(track.get("duration_s") or 0) if result else 0.0
    cached = bool(timing.get("track_cached"))
    made = 0 if (error or cached or not audio) else 1
    usage.record(agent, "synthesize", not error, time.perf_counter() - started,
                 audio_s=audio, tracks=made, error=error)


def _run_job(jid, url, force, agent):
    started = time.perf_counter()
    def prog(stage, pct):
        with JOBS_LOCK: JOBS[jid].update(stage=stage, progress=pct)
    try:
        r = pipeline.synthesize(url, force=force, progress=prog)
        with JOBS_LOCK: JOBS[jid].update(state="done", stage="done", progress=100, result=_result(r))
        note_synthesize(agent, started, r)
    except Exception as e:
        with JOBS_LOCK: JOBS[jid].update(state="error", stage="error", error=str(e))
        note_synthesize(agent, started, error=str(e))


def read_json_object(handler, limit):
    """Read a JSON object body. Returns (body, True) after the error response is sent."""
    try:
        n = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        n = 0
    if n > limit:
        handler._json(413, {"error": "body too large"})
        return None, True
    raw = handler.rfile.read(n) if n else b""
    if not raw:
        return {}, False
    try:
        body = json.loads(raw)
    except Exception:
        handler._json(400, {"error": "body must be JSON"})
        return None, True
    if not isinstance(body, dict):
        handler._json(400, {"error": "body must be a JSON object"})
        return None, True
    return body, False


def route_agents_and_usage_get(handler, path):
    """GET /api/agents and GET /api/usage. These polls are not usage events."""
    if path == "/api/agents":
        handler._json(200, agents.list_agents())
        return True
    if path == "/api/usage":
        handler._json(200, usage.summary())
        return True
    return False


def route_mood_get(handler, parsed):
    """GET /api/mood, recorded as tool get_mood."""
    if parsed.path != "/api/mood":
        return False
    qs = urllib.parse.parse_qs(parsed.query)
    url = (qs.get("url") or [""])[0].strip()
    who = usage.caller({"agent_id": (qs.get("agent_id") or [""])[0]}, handler.headers)
    started = time.perf_counter()
    if not url:
        usage.record(who, "get_mood", False, 0, error="missing url")
        handler._json(400, {"error": "missing ?url="})
        return True
    try:
        mood, debug, page, timing = pipeline.mood_for(url)
        usage.record(who, "get_mood", True, time.perf_counter() - started)
        handler._json(200, dict(mood, _meta={"page": page, "analysis": debug, "timing": timing}))
    except IngestError as e:
        usage.record(who, "get_mood", False, time.perf_counter() - started, error=str(e))
        handler._json(422, {"error": str(e)})
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        usage.record(who, "get_mood", False, time.perf_counter() - started, error=msg)
        handler._json(500, {"error": msg})
    return True


def route_agents_post(handler, path):
    """POST /api/agents. First contact registers; later posts merge."""
    if path != "/api/agents":
        return False
    body, failed = read_json_object(handler, 8000)
    if failed:
        return True
    who = usage.caller(body, handler.headers)
    if who == "anonymous":
        who = usage.caller({"agent_id": body.get("id")}, None)
    started = time.perf_counter()
    try:
        rec = agents.report(body)
    except agents.AgentError as e:
        usage.record(who, "report_status", False, time.perf_counter() - started, error=str(e))
        handler._json(e.status, {"error": str(e)})
        return True
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        usage.record(who, "report_status", False, time.perf_counter() - started, error=msg)
        handler._json(500, {"error": msg})
        return True
    usage.record(who, "report_status", True, time.perf_counter() - started)
    handler._json(200, rec)
    return True


def route_synthesize_post(handler, path):
    """POST /api/synthesize. Credits tool name synthesize (sync and async)."""
    if path != "/api/synthesize":
        return False
    body, failed = read_json_object(handler, 10000)
    if failed:
        return True
    url = str(body.get("url") or "").strip()
    who = usage.caller(body, handler.headers)
    if not url:
        usage.record(who, "synthesize", False, 0, error="missing url")
        handler._json(400, {"error": "missing url"})
        return True
    force = bool(body.get("force"))
    if body.get("async"):
        jid = uuid.uuid4().hex[:12]
        with JOBS_LOCK:
            JOBS[jid] = {"id": jid, "url": url, "state": "running", "stage": "queued", "progress": 0, "started": time.time()}
        threading.Thread(target=_run_job, args=(jid, url, force, who), daemon=True).start()
        handler._json(202, {"job_id": jid, "poll": f"/api/jobs/{jid}"})
        return True
    started = time.perf_counter()
    try:
        r = pipeline.synthesize(url, force=force)
    except IngestError as e:
        note_synthesize(who, started, error=str(e))
        handler._json(422, {"error": str(e)})
        return True
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        note_synthesize(who, started, error=msg)
        handler._json(500, {"error": msg})
        return True
    note_synthesize(who, started, r)
    handler._json(200, _result(r))
    return True


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def end_headers(self):
        if self.path.startswith("/api/") or self.path.endswith(".json"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/health":
            return self._json(200, {"ok": True, "jobs": len(JOBS)})
        if route_agents_and_usage_get(self, u.path):
            return
        if route_mood_get(self, u):
            return
        if u.path.startswith("/api/jobs/"):
            with JOBS_LOCK: j = dict(JOBS.get(u.path.rsplit("/", 1)[-1]) or {})
            return self._json(200 if j else 404, j or {"error": "no such job"})
        if u.path.startswith("/api/"):
            return self._json(404, {"error": "unknown endpoint"})
        return super().do_GET()

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if route_agents_post(self, u.path):
            return
        if route_synthesize_post(self, u.path):
            return
        return self._json(404, {"error": "unknown endpoint"})


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=53343); ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler); srv.daemon_threads = True
    print(f"Lofi control room on http://{a.host}:{a.port}/dashboard/  (API /api/*)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
