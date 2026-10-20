"""engine/server.py - serves the dashboard (static, same paths as before) + the URL-to-Lofi API.

  GET  /api/health
  GET  /api/mood?url=...                 -> PRD mood JSON (+ analysis/timing under _meta)
  GET  /api/agents                       -> live agents (offline computed from last_update)
  GET  /api/usage                        -> local usage totals (no network)
  POST /api/agents                       -> register or update one agent
  GET  /api/topics                       -> cached topics + trend chips (post texts omitted)
  POST /api/synthesize {"url": ...}      -> {"mood": {...PRD...}, "track_url": "/tracks/....mp3", ...}
       add "async": true                 -> {"job_id": ...}; poll GET /api/jobs/<id>
       add "force": true                 -> re-render even if this exact track exists
  POST /api/synthesize_topic {"topic": "AI agents"} or {"trend": "..."} or {"posts": [...]}
       unknown topic -> 404 {"error_kind": "topic_not_found"}
       optional "agent_id" (or header X-Agent-Id) is who the usage log credits
Run from the repo root: ./run.sh   or   python3 engine/server.py [--port 53343]
Static files are only dashboard/, samples/, tracks/ and status/. `/` redirects to `/dashboard/`.
Dotfiles are not served. API routes are /api/*.
Binds to 127.0.0.1 by default. Host and Origin are allowed for localhost, 127.0.0.1, ::1,
the bind address, and LOFI_PUBLIC_BASE. Missing or unparseable values are allowed and logged.
No auth secrets.

Live agents and the usage meter live in the named helpers below (route_agents_*,
route_mood_get, route_synthesize_post, note_synthesize). Topic search is
route_topics_get / route_synthesize_topic_post / note_topic. Polls of /api/agents,
/api/usage, and /api/topics are not recorded.
"""
import argparse, json, os, sys, threading, time, uuid, urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import agents, pipeline, usage  # noqa: E402
from engine.ingest import IngestError  # noqa: E402

JOBS = {}
JOBS_LOCK = threading.Lock()
STATIC_ROOTS = {"dashboard", "samples", "tracks", "status"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def public_hostname():
    """Host from LOFI_PUBLIC_BASE, or None. Used as an extra allowlist entry."""
    raw = os.environ.get("LOFI_PUBLIC_BASE", "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    try:
        host = urllib.parse.urlsplit(raw).hostname
    except ValueError:
        return None
    return host.lower().rstrip(".") if host else None


def split_host(value):
    """Return a hostname, or None when the header is not a simple host[:port]."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            return None
        rest = text[end + 1:]
        if rest and not rest.startswith(":"):
            return None
        return text[1:end].lower().rstrip(".") or None
    if text.count(":") > 1:
        return None
    host = text.split(":", 1)[0].lower().rstrip(".")
    return host or None


def origin_host(value):
    """('missing'|'empty'|'null'|'bad'|'ok', hostname or None)."""
    if value is None:
        return "missing", None
    text = str(value).strip()
    if not text:
        return "empty", None
    if text.lower() == "null":
        return "null", None
    try:
        parts = urllib.parse.urlsplit(text)
        host = parts.hostname
    except ValueError:
        return "bad", None
    if parts.scheme not in ("http", "https") or not host:
        return "bad", None
    return "ok", host.lower().rstrip(".")


def request_gate(host_header, origin_header, allowed):
    """'allow', 'allow-log', or 'deny', plus a short reason.

    Clear foreign hosts and origins are refused. Anything we cannot parse is
    allowed and logged so a proxy or older client keeps working.
    """
    if host_header is None or not str(host_header).strip():
        host_kind, host = "missing", None
    else:
        host = split_host(host_header)
        host_kind = "ok" if host else "bad"
    if host_kind == "ok" and host not in allowed:
        return "deny", "host"
    kind, ohost = origin_host(origin_header)
    if kind == "ok" and ohost not in allowed:
        return "deny", "origin"
    if host_kind == "ok" and kind in ("ok", "missing"):
        return "allow", ""
    bits = []
    if host_kind != "ok":
        bits.append("host " + host_kind)
    if kind not in ("ok", "missing"):
        bits.append("origin " + kind)
    return "allow-log", ", ".join(bits)


def static_allowed(url_path):
    """True when url_path is a file under dashboard/, samples/, tracks/ or status/."""
    path = url_path.split("?", 1)[0].split("#", 1)[0]
    try:
        path = urllib.parse.unquote(path, errors="strict")
    except UnicodeDecodeError:
        return False
    if "\\" in path or "\x00" in path:
        return False
    parts = []
    for seg in path.split("/"):
        if seg in ("", "."):
            continue
        if seg == ".." or seg.startswith("."):
            return False
        parts.append(seg)
    if not parts or parts[0] not in STATIC_ROOTS:
        return False
    full = os.path.normpath(os.path.join(ROOT, *parts))
    real = os.path.realpath(full)
    root = os.path.realpath(ROOT)
    try:
        if os.path.commonpath([real, root]) != root:
            return False
    except ValueError:
        return False
    rel = os.path.relpath(real, root)
    rel_parts = rel.split(os.sep)
    if rel_parts[0] not in STATIC_ROOTS:
        return False
    if any(part.startswith(".") for part in rel_parts):
        return False
    return True


def _result(r):
    out = {"mood": r["mood"], "track_url": r["track"]["url"], "track": r["track"], "page": r["page"],
           "analysis": r["analysis"], "timing": r["timing"]}
    if r.get("topic"):
        out["topic"] = r["topic"]
    return out


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


def topics_catalog():
    """x_topics.json for the suggestion chips. Post texts stay on disk."""
    data = pipeline.load_topics()
    topics = []
    for topic in data.get("topics") or []:
        row = {k: v for k, v in topic.items() if k != "posts"}
        row["post_count"] = len(topic.get("posts") or [])
        topics.append(row)
    return {
        "topics": topics,
        "trends": data.get("trends") or {},
        "updated": data.get("updated"),
        "about": data.get("about"),
        "live_search": bool(os.environ.get("X_BEARER_TOKEN")),
    }


def note_topic(agent, started, result=None, error=""):
    """Credit one synthesize_topic call. Live X search adds x_reads; the cache does not."""
    track = (result or {}).get("track") or {}
    timing = (result or {}).get("timing") or {}
    topic = (result or {}).get("topic") or {}
    audio = float(track.get("duration_s") or 0) if result else 0.0
    cached = bool(timing.get("track_cached"))
    made = 0 if (error or cached or not audio) else 1
    reads = int(topic.get("x_reads") or 0) if result and not error else 0
    usage.record(agent, "synthesize_topic", not error, time.perf_counter() - started,
                 audio_s=audio, tracks=made, x_reads=reads, error=error)


def _run_topic_job(jid, topic, posts, force, agent):
    started = time.perf_counter()
    def prog(stage, pct):
        with JOBS_LOCK: JOBS[jid].update(stage=stage, progress=pct)
    try:
        r = pipeline.synthesize_topic(topic, posts=posts, force=force, progress=prog)
        with JOBS_LOCK: JOBS[jid].update(state="done", stage="done", progress=100, result=_result(r))
        note_topic(agent, started, r)
    except Exception as e:
        kind = "topic_not_found" if isinstance(e, pipeline.TopicNotFound) else None
        with JOBS_LOCK: JOBS[jid].update(state="error", stage="error", error=str(e), error_kind=kind)
        note_topic(agent, started, error=str(e))


def route_topics_get(handler, path):
    """GET /api/topics. Not a usage event."""
    if path != "/api/topics":
        return False
    handler._json(200, topics_catalog())
    return True


def _topic_request(body):
    topic = str(body.get("topic") or body.get("trend") or "").strip()[:120]
    posts = body.get("posts")
    if posts is not None and (not isinstance(posts, list) or not all(isinstance(p, str) for p in posts)):
        return None, None, "posts must be a list of strings"
    if not topic and not posts:
        return None, None, "missing topic"
    cleaned = [p[:1000] for p in (posts or [])[:100]] or None
    return topic or "custom posts", cleaned, None


def route_synthesize_topic_post(handler, path):
    """POST /api/synthesize_topic. Records tool name synthesize_topic."""
    if path != "/api/synthesize_topic":
        return False
    body, failed = read_json_object(handler, 400000)
    if failed:
        return True
    topic, posts, err = _topic_request(body)
    who = usage.caller(body, handler.headers)
    if err:
        usage.record(who, "synthesize_topic", False, 0, error=err)
        handler._json(400, {"error": err})
        return True
    force = bool(body.get("force"))
    if body.get("async"):
        jid = uuid.uuid4().hex[:12]
        with JOBS_LOCK:
            JOBS[jid] = {"id": jid, "url": topic, "state": "running", "stage": "queued", "progress": 0, "started": time.time()}
        threading.Thread(target=_run_topic_job, args=(jid, topic, posts, force, who), daemon=True).start()
        handler._json(202, {"job_id": jid, "poll": f"/api/jobs/{jid}"})
        return True
    started = time.perf_counter()
    try:
        r = pipeline.synthesize_topic(topic, posts=posts, force=force)
    except pipeline.TopicNotFound as e:
        note_topic(who, started, error=str(e))
        handler._json(404, {"error": str(e), "error_kind": "topic_not_found"})
        return True
    except IngestError as e:
        note_topic(who, started, error=str(e))
        handler._json(422, {"error": str(e)})
        return True
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        note_topic(who, started, error=msg)
        handler._json(500, {"error": msg})
        return True
    note_topic(who, started, r)
    handler._json(200, _result(r))
    return True


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def _allowed_names(self):
        names = set(LOCAL_HOSTS)
        public = public_hostname()
        if public:
            names.add(public)
        try:
            bind = self.server.server_address[0]
        except Exception:
            bind = ""
        if bind and bind not in {"0.0.0.0", "::"}:
            names.add(str(bind).lower().rstrip("."))
        return names

    def _gate(self):
        """False when a response was already sent."""
        decision, why = request_gate(self.headers.get("Host"), self.headers.get("Origin"), self._allowed_names())
        if decision == "deny":
            self.log_message("rejected %s host=%s origin=%s", why, self.headers.get("Host"), self.headers.get("Origin"))
            self._json(403, {"error": "forbidden"})
            return False
        if decision == "allow-log":
            self.log_message("allowing request (%s) host=%s origin=%s", why, self.headers.get("Host"), self.headers.get("Origin"))
        ctype = self.headers.get("Content-Type")
        if self.command == "POST" and ctype and ctype.split(";", 1)[0].strip().lower() != "application/json":
            self.log_message("POST Content-Type %s is not application/json; allowing", ctype.split(";", 1)[0].strip())
        return True

    def _redirect_root(self, parsed):
        loc = "/dashboard/"
        if parsed.query:
            loc += "?" + parsed.query
        self.send_response(302)
        self.send_header("Location", loc)
        self.send_header("Content-Length", "0")
        self.end_headers()

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

    def list_directory(self, path):
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        return None

    def translate_path(self, path):
        if not static_allowed(path):
            return os.path.join(ROOT, ".not-served")
        return super().translate_path(path)

    def do_GET(self):
        if not self._gate():
            return
        u = urllib.parse.urlparse(self.path)
        if u.path in ("", "/"):
            return self._redirect_root(u)
        if u.path == "/api/health":
            return self._json(200, {"ok": True, "jobs": len(JOBS)})
        if route_agents_and_usage_get(self, u.path):
            return
        if route_topics_get(self, u.path):
            return
        if route_mood_get(self, u):
            return
        if u.path.startswith("/api/jobs/"):
            with JOBS_LOCK: j = dict(JOBS.get(u.path.rsplit("/", 1)[-1]) or {})
            return self._json(200 if j else 404, j or {"error": "no such job"})
        if u.path.startswith("/api/"):
            return self._json(404, {"error": "unknown endpoint"})
        if not static_allowed(u.path):
            return self._json(403, {"error": "forbidden"})
        return super().do_GET()

    def do_HEAD(self):
        if not self._gate():
            return
        u = urllib.parse.urlparse(self.path)
        if u.path in ("", "/"):
            return self._redirect_root(u)
        if u.path.startswith("/api/"):
            return self._json(404, {"error": "unknown endpoint"})
        if not static_allowed(u.path):
            return self._json(403, {"error": "forbidden"})
        return super().do_HEAD()

    def do_POST(self):
        if not self._gate():
            return
        u = urllib.parse.urlparse(self.path)
        if route_agents_post(self, u.path):
            return
        if route_synthesize_topic_post(self, u.path):
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
