"""engine/server.py - serves the dashboard (static, same paths as before) + the URL-to-Lofi API.

  GET  /api/health
  GET  /api/mood?url=...                 -> PRD mood JSON (+ analysis/timing under _meta)
  POST /api/synthesize {"url": ...}      -> {"mood": {...PRD...}, "track_url": "/tracks/....mp3", ...}
       add "async": true                 -> {"job_id": ...}; poll GET /api/jobs/<id>
       add "force": true                 -> re-render even if this exact track exists
Run from the repo root: ./run.sh   or   python3 engine/server.py [--port 53343]
The process serves the repo root. Open /dashboard/ for the control room. API routes are /api/*.
"""
import argparse, json, os, sys, threading, time, uuid, urllib.parse
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import pipeline  # noqa: E402
from engine.ingest import IngestError  # noqa: E402

JOBS = {}
JOBS_LOCK = threading.Lock()


def _result(r):
    return {"mood": r["mood"], "track_url": r["track"]["url"], "track": r["track"], "page": r["page"],
            "analysis": r["analysis"], "timing": r["timing"]}


def _run_job(jid, url, force):
    def prog(stage, pct):
        with JOBS_LOCK: JOBS[jid].update(stage=stage, progress=pct)
    try:
        r = pipeline.synthesize(url, force=force, progress=prog)
        with JOBS_LOCK: JOBS[jid].update(state="done", stage="done", progress=100, result=_result(r))
    except Exception as e:
        with JOBS_LOCK: JOBS[jid].update(state="error", stage="error", error=str(e))


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
        if u.path == "/api/mood":
            url = (urllib.parse.parse_qs(u.query).get("url") or [""])[0].strip()
            if not url: return self._json(400, {"error": "missing ?url="})
            try:
                mood, debug, page, timing = pipeline.mood_for(url)
                return self._json(200, dict(mood, _meta={"page": page, "analysis": debug, "timing": timing}))
            except IngestError as e:
                return self._json(422, {"error": str(e)})
            except Exception as e:
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        if u.path.startswith("/api/jobs/"):
            with JOBS_LOCK: j = dict(JOBS.get(u.path.rsplit("/", 1)[-1]) or {})
            return self._json(200 if j else 404, j or {"error": "no such job"})
        if u.path.startswith("/api/"):
            return self._json(404, {"error": "unknown endpoint"})
        return super().do_GET()

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/api/synthesize":
            return self._json(404, {"error": "unknown endpoint"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 10000: return self._json(413, {"error": "body too large"})
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json(400, {"error": "body must be JSON like {\"url\": \"https://...\"}"})
        url = str(body.get("url") or "").strip()
        if not url: return self._json(400, {"error": "missing url"})
        force = bool(body.get("force"))
        if body.get("async"):
            jid = uuid.uuid4().hex[:12]
            with JOBS_LOCK:
                JOBS[jid] = {"id": jid, "url": url, "state": "running", "stage": "queued", "progress": 0, "started": time.time()}
            threading.Thread(target=_run_job, args=(jid, url, force), daemon=True).start()
            return self._json(202, {"job_id": jid, "poll": f"/api/jobs/{jid}"})
        try:
            return self._json(200, _result(pipeline.synthesize(url, force=force)))
        except IngestError as e:
            return self._json(422, {"error": str(e)})
        except Exception as e:
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=53343); ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler); srv.daemon_threads = True
    print(f"Lofi control room on http://{a.host}:{a.port}/dashboard/  (API /api/*)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
