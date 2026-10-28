"""engine/server.py - serves the dashboard (static, same paths as before) + the URL-to-Lofi API.

  GET  /api/health
  GET  /api/mood?url=...                 -> PRD mood JSON (+ analysis/timing under _meta)
  GET  /api/agents                       -> live agents (offline computed from last_update)
  GET  /api/usage                        -> local usage totals (no network)
  POST /api/agents                       -> register or update one agent
  GET  /api/topics                       -> cached topics + trend chips (post texts omitted)
  POST /api/synthesize {"url": ...}      -> {"mood": {...PRD...}, "track_url": "/tracks/....mp3", ...}
       optional "vocals": "none"|"summary"|"song" (default none; ignored if Chatterbox is missing)
       add "async": true                 -> {"job_id": ...}; poll GET /api/jobs/<id>
       add "force": true                 -> re-render even if this exact track exists
  POST /api/synthesize_topic {"topic": "AI agents", "source": "web"|"x"}
       source defaults to web (engine/topic_web.py). "x" keeps the X-posts path.
       empty topic -> 400 {"error_kind": "empty_topic"}; unknown -> 404 {"error_kind": "topic_not_found"}
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
# One local machine renders a handful of tracks at once. Callers past the cap
# get 429 instead of another thread. Finished jobs are dropped so the table
# cannot grow for the life of the process.
MAX_RUNNING = 4
MAX_RETAINED_JOBS = 64
JOB_RETAIN_S = 30 * 60
_SLOTS = threading.BoundedSemaphore(MAX_RUNNING)
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
    if r.get("vocals"):
        out["vocals"] = r["vocals"]
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


def _vocal_request(body):
    """vocals defaults to none. Anything else is ignored so a typo cannot change the instrumental."""
    mode = str(body.get("vocals") or "none").strip().lower()
    if mode not in ("summary", "song"):
        mode = "none"
    script = body.get("script")
    if script is None:
        script = body.get("lyrics")
    if script is not None:
        script = str(script)[:4000]
    return mode, script


def _run_job(jid, url, force, agent, vocals="none", script=None):
    """Render, then return the terminal job update. The caller publishes it after the slot is free."""
    started = time.perf_counter()
    def prog(stage, pct):
        with JOBS_LOCK: JOBS[jid].update(stage=stage, progress=pct)
    try:
        r = pipeline.synthesize(url, force=force, progress=prog, vocals=vocals, script=script, agent=agent)
        note_synthesize(agent, started, r)
        return {"state": "done", "stage": "done", "progress": 100, "result": _result(r)}
    except Exception as e:
        note_synthesize(agent, started, error=str(e))
        return {"state": "error", "stage": "error", "error": str(e)}


def _prune_jobs_locked(now=None):
    now = time.time() if now is None else now
    expired = [
        jid for jid, job in JOBS.items()
        if job.get("state") != "running" and now - float(job.get("started") or now) > JOB_RETAIN_S
    ]
    for jid in expired:
        JOBS.pop(jid, None)
    overflow = len(JOBS) - MAX_RETAINED_JOBS
    if overflow <= 0:
        return
    finished = sorted(
        (float(job.get("started") or 0), jid)
        for jid, job in JOBS.items() if job.get("state") != "running"
    )
    for _started, jid in finished[:overflow]:
        JOBS.pop(jid, None)


def _reject_busy(handler, who, tool):
    usage.record(who, tool, False, 0, error="busy")
    handler._json(429, {"error": "too many tracks are rendering"})


def _spawn_job(jid, record, target, args):
    """Start one tracked job. False when every render slot is already taken."""
    with JOBS_LOCK:
        _prune_jobs_locked()
        if not _SLOTS.acquire(blocking=False):
            return False
        JOBS[jid] = record
    def run():
        update = None
        try:
            update = target(*args)
        except Exception as e:
            update = {"state": "error", "stage": "error", "error": f"{type(e).__name__}: {e}"}
        finally:
            _SLOTS.release()
        if update:
            with JOBS_LOCK:
                job = JOBS.get(jid)
                if job is not None:
                    job.update(update)
    try:
        threading.Thread(target=run, daemon=True).start()
    except Exception:
        _SLOTS.release()
        with JOBS_LOCK:
            JOBS.pop(jid, None)
        raise
    return True


def read_json_object(handler, limit):
    """Read a JSON object body. Returns (body, True) after the error response is sent."""
    raw_len = handler.headers.get("Content-Length")
    if raw_len is None:
        raw = _read_arrived(handler, limit)
        if raw is None:
            _length_error(handler, 413, "body too large")
            return None, True
        if raw:
            handler.close_connection = True
    else:
        try:
            n = int(str(raw_len).strip())
        except ValueError:
            _length_error(handler, 400, "bad content length")
            return None, True
        if n < 0:
            _length_error(handler, 400, "bad content length")
            return None, True
        if n > limit:
            _length_error(handler, 413, "body too large")
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


def _length_error(handler, code, message):
    handler.close_connection = True
    handler._json(code, {"error": message})


def _read_arrived(handler, limit):
    """Bytes already received, or None when more than limit are waiting.

    A missing Content-Length must not read until the client closes. The client
    is blocked on the response, so that read never finishes.
    """
    sock = handler.connection
    previous = sock.gettimeout()
    sock.setblocking(False)
    try:
        data = bytearray()
        while len(data) <= limit:
            try:
                chunk = handler.rfile.read1(min(65536, limit + 1 - len(data)))
            except (BlockingIOError, TimeoutError):
                break
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > limit:
            return None
        return bytes(data)
    finally:
        sock.settimeout(previous)


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
    vocals, script = _vocal_request(body)
    if body.get("async"):
        jid = uuid.uuid4().hex[:12]
        record = {"id": jid, "url": url, "state": "running", "stage": "queued", "progress": 0, "started": time.time()}
        if not _spawn_job(jid, record, _run_job, (jid, url, force, who, vocals, script)):
            _reject_busy(handler, who, "synthesize")
            return True
        handler._json(202, {"job_id": jid, "poll": f"/api/jobs/{jid}"})
        return True
    if not _SLOTS.acquire(blocking=False):
        _reject_busy(handler, who, "synthesize")
        return True
    started = time.perf_counter()
    try:
        try:
            r = pipeline.synthesize(url, force=force, vocals=vocals, script=script, agent=who)
        except IngestError as e:
            note_synthesize(who, started, error=str(e))
            response = (422, {"error": str(e)})
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            note_synthesize(who, started, error=msg)
            response = (500, {"error": msg})
        else:
            note_synthesize(who, started, r)
            response = (200, _result(r))
    finally:
        _SLOTS.release()
    handler._json(*response)
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
        "default_source": "web",
        "sources": ["web", "x"],
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


def _run_topic_job(jid, topic, posts, force, agent, source="web", vocals="none", script=None):
    """Same contract as _run_job: return the terminal update, do not publish it."""
    started = time.perf_counter()
    def prog(stage, pct):
        with JOBS_LOCK: JOBS[jid].update(stage=stage, progress=pct)
    try:
        r = pipeline.synthesize_topic(topic, posts=posts, force=force, progress=prog, source=source,
                                     vocals=vocals, script=script, agent=agent)
        note_topic(agent, started, r)
        return {"state": "done", "stage": "done", "progress": 100, "result": _result(r)}
    except Exception as e:
        kind = getattr(e, "error_kind", None)
        if kind is None and isinstance(e, pipeline.TopicNotFound):
            kind = "topic_not_found"
        note_topic(agent, started, error=str(e))
        return {"state": "error", "stage": "error", "error": str(e), "error_kind": kind}


def route_topics_get(handler, path):
    """GET /api/topics. Not a usage event."""
    if path != "/api/topics":
        return False
    handler._json(200, topics_catalog())
    return True


def _topic_request(body):
    """-> (topic, posts, source, err). source is web (default) or x. posts apply only to x."""
    from engine.topic_web import BadSource, EmptyTopic, normalize_source, normalize_topic
    try:
        source = normalize_source(body.get("source", "web"))
    except BadSource as e:
        return None, None, "web", str(e)
    posts = body.get("posts")
    if posts is not None and (not isinstance(posts, list) or not all(isinstance(p, str) for p in posts)):
        return None, None, source, "posts must be a list of strings"
    topic = str(body.get("topic") or body.get("trend") or "").strip()[:120]
    if source == "x" and posts and not topic:
        return "custom posts", [p[:1000] for p in posts[:100]], source, None
    try:
        topic = normalize_topic(topic)
    except EmptyTopic as e:
        return None, None, source, str(e)
    if source != "x":
        posts = None
    cleaned = [p[:1000] for p in (posts or [])[:100]] or None
    return topic, cleaned, source, None


def route_synthesize_topic_post(handler, path):
    """POST /api/synthesize_topic. Records tool name synthesize_topic."""
    if path != "/api/synthesize_topic":
        return False
    body, failed = read_json_object(handler, 400000)
    if failed:
        return True
    topic, posts, source, err = _topic_request(body)
    who = usage.caller(body, handler.headers)
    if err:
        usage.record(who, "synthesize_topic", False, 0, error=err)
        kind = "empty_topic" if err.startswith("Please enter a topic") else (
            "bad_source" if err.startswith("Source must") else None)
        payload = {"error": err}
        if kind:
            payload["error_kind"] = kind
        handler._json(400, payload)
        return True
    force = bool(body.get("force"))
    vocals, script = _vocal_request(body)
    if body.get("async"):
        jid = uuid.uuid4().hex[:12]
        record = {"id": jid, "url": topic, "source": source, "state": "running", "stage": "queued",
                  "progress": 0, "started": time.time()}
        if not _spawn_job(jid, record, _run_topic_job, (jid, topic, posts, force, who, source, vocals, script)):
            _reject_busy(handler, who, "synthesize_topic")
            return True
        handler._json(202, {"job_id": jid, "poll": f"/api/jobs/{jid}"})
        return True
    if not _SLOTS.acquire(blocking=False):
        _reject_busy(handler, who, "synthesize_topic")
        return True
    started = time.perf_counter()
    from engine.topic_web import BadSource, EmptyTopic
    try:
        try:
            r = pipeline.synthesize_topic(topic, posts=posts, force=force, source=source,
                                         vocals=vocals, script=script, agent=who)
        except (EmptyTopic, BadSource) as e:
            note_topic(who, started, error=str(e))
            response = (e.http_status, {"error": str(e), "error_kind": e.error_kind})
        except pipeline.TopicNotFound as e:
            note_topic(who, started, error=str(e))
            response = (404, {"error": str(e), "error_kind": "topic_not_found"})
        except IngestError as e:
            note_topic(who, started, error=str(e))
            response = (422, {"error": str(e)})
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            note_topic(who, started, error=msg)
            response = (500, {"error": msg})
        else:
            note_topic(who, started, r)
            response = (200, _result(r))
    finally:
        _SLOTS.release()
    handler._json(*response)
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
        else:
            self.send_header("Accept-Ranges", "bytes")
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
            jid = u.path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                _prune_jobs_locked()
                j = dict(JOBS.get(jid) or {})
            return self._json(200 if j else 404, j or {"error": "no such job"})
        if u.path.startswith("/api/"):
            return self._json(404, {"error": "unknown endpoint"})
        if not static_allowed(u.path):
            return self._json(403, {"error": "forbidden"})
        if self.headers.get("Range"):
            return self._byte_range()
        return super().do_GET()

    def _byte_range(self):
        """One byte range for a static file: 206, 416, or the normal handler."""
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            return super().do_GET()
        size = os.path.getsize(path)
        start = end = None
        spec = (self.headers.get("Range") or "").strip()
        try:
            if spec.lower().startswith("bytes=") and "," not in spec:
                lo, sep, hi = spec[6:].strip().partition("-")
                if sep and size:
                    if lo == "":
                        n = int(hi)
                        if n > 0:
                            start, end = max(0, size - n), size - 1
                    else:
                        start, end = int(lo), (int(hi) if hi else size - 1)
                        end = min(end, size - 1)
                        if start < 0 or start >= size or start > end:
                            start = end = None
        except ValueError:
            start = end = None
        if start is None:
            self.send_response(416)
            self.send_header("Content-Range", "bytes */%d" % size)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(end - start + 1)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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
