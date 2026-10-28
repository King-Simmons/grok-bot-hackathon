"""mcp/lofi_mcp_server.py - MCP server for the URL-to-Lofi Synthesizer (python `mcp` SDK v2).

Tools:
  synthesize_url(url, force=False, agent_id="") -> PRD mood JSON + track file path/URL
  get_mood_analysis(url, agent_id="")           -> PRD mood JSON only (no audio)
  synthesize_topic(topic, posts=None, force=False, source="web", agent_id="")
                                                -> lofi from public web pages (default) or X posts (source="x")
  report_status(...)                            -> register or update one agent on the dashboard
  list_agents()                                 -> agents currently on disk, with online/offline
  get_usage()                                   -> local usage totals (no network when the dashboard is down)

Transports:
  stdio (default):        python3 mcp/lofi_mcp_server.py
  streamable HTTP:        python3 mcp/lofi_mcp_server.py --http --port 53350   (endpoint /mcp)
Env: LOFI_PUBLIC_BASE (default http://127.0.0.1:53343) is the app server and the track URL prefix.
LOFI_MCP_TRANSPORT is auto (default), http, or local. auto calls the app server when /api/health
is ok and otherwise runs the engine in this process. http never falls back. local never calls out.
LOFI_MCP_HTTP_TIMEOUT (seconds, default 15) bounds one HTTP call. LOFI_MCP_JOB_WAIT (default 180)
bounds how long a tool polls a job before it returns still_running and the job id.
A failure result is {"ok": false, "error_kind": "...", "error": "..."}.
agent_id, when passed, is who the usage log credits. Omit it and the credit is "anonymous".
The live-agent tools are the marked section at the bottom of this file.
"""
import argparse, json, os, re, socket, sys, threading, time, urllib.error, urllib.parse, urllib.request, uuid
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if sys.path and os.path.abspath(sys.path[0]) == os.path.dirname(os.path.abspath(__file__)):
    sys.path.pop(0)  # don't let this folder (named "mcp") shadow the mcp package
sys.path.insert(0, ROOT)

import anyio  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

_MAX_BODY = 2_000_000
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_STATES = ("running", "idle", "done", "error")
_TOPIC_KINDS = {"empty_topic", "bad_source", "topic_not_found"}
_EMPTY_TOPIC = 'Please enter a topic to learn. Try a short phrase like "lofi" or "space telescopes".'
_BAD_SOURCE = 'Source must be "web" or "x". Web is the default; X is optional.'


def _positive_float(name, default):
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return value


def _http_timeout():
    return _positive_float("LOFI_MCP_HTTP_TIMEOUT", 15.0)


def _job_wait():
    return _positive_float("LOFI_MCP_JOB_WAIT", 180.0)


def _poll_interval():
    return _positive_float("LOFI_MCP_POLL_INTERVAL", 0.4)


def _base():
    return os.environ.get("LOFI_PUBLIC_BASE", "http://127.0.0.1:53343").strip().rstrip("/")


def _base_ok(base):
    return base.startswith("http://") or base.startswith("https://")


def _transport_mode():
    mode = os.environ.get("LOFI_MCP_TRANSPORT", "auto").strip().lower()
    if mode not in ("auto", "http", "local"):
        return "auto"
    return mode


def _fail(kind, message, **extra):
    out = {"ok": False, "error_kind": kind, "error": message}
    out.update(extra)
    return out


def _deps():
    from engine import agents, pipeline, usage
    return agents, pipeline, usage


def _who(agent_id):
    _agents, _pipeline, usage = _deps()
    return usage.caller({"agent_id": agent_id or ""})


def _note(who, tool, started, *, ok, error="", audio_s=0.0, tracks=0, x_reads=0):
    _agents, _pipeline, usage = _deps()
    usage.record(who, tool, ok, time.perf_counter() - started, audio_s=audio_s, tracks=tracks,
                 x_reads=x_reads, error=(error or "")[:200])


def _transport_fail(exc):
    reason = getattr(exc, "reason", exc)
    if isinstance(exc, (TimeoutError, socket.timeout)) or isinstance(reason, (TimeoutError, socket.timeout)):
        return _fail("timeout", "The app server did not respond in time.")
    text = str(reason).lower()
    if "timed out" in text or "timeout" in text:
        return _fail("timeout", "The app server did not respond in time.")
    return _fail("server_down", "The app server is unreachable.")


def _http_json(method, path, body=None, timeout=None):
    base = _base()
    if not _base_ok(base):
        return ("err", _fail("bad_args", "LOFI_PUBLIC_BASE must be an http or https URL."))
    data = None
    headers = {"Accept": "application/json", "User-Agent": "lofi-mcp/0.1"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout or _http_timeout()) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read(_MAX_BODY + 1)
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read(_MAX_BODY + 1)
        except Exception:
            raw = b""
    except Exception as exc:
        return ("err", _transport_fail(exc))
    if len(raw) > _MAX_BODY:
        return ("err", _fail("bad_response", "App server response was too large.", http_status=status))
    if not raw:
        return ("err", _fail("bad_response", "App server returned an empty body.", http_status=status))
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception:
        if status >= 400:
            return ("err", _fail(
                "http_error",
                f"HTTP {status} from the app server (response was not JSON).",
                http_status=status,
            ))
        return ("err", _fail("bad_response", "App server response was not JSON.", http_status=status))
    if status >= 400:
        message = f"HTTP {status} from the app server."
        extra = {"http_status": status}
        if isinstance(obj, dict):
            if isinstance(obj.get("error"), str) and obj["error"].strip():
                message = obj["error"]
            if obj.get("error_kind"):
                extra["server_error_kind"] = obj["error_kind"]
        return ("err", _fail("http_error", message, **extra))
    if not isinstance(obj, dict):
        return ("err", _fail("bad_response", "App server response was not a JSON object.", http_status=status))
    return ("ok", status, obj)


def _health_up():
    outcome = _http_json("GET", "/api/health", timeout=min(0.5, _http_timeout()))
    if outcome[0] != "ok":
        return False
    return isinstance(outcome[2], dict) and outcome[2].get("ok") is True


def _use_http():
    # auto keeps the in-process tools working when the dashboard is not running.
    # http never falls back. local never calls out.
    mode = _transport_mode()
    if mode == "http":
        return True
    if mode == "local":
        return False
    return _health_up()


def _ticket(obj):
    if not isinstance(obj, dict):
        return None
    job_id = obj.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        return None
    if any(key in obj for key in ("mood", "bots", "all_time", "musical_attributes", "page_summary")):
        return None
    if obj.get("state") == "done":
        return None
    poll = obj.get("poll")
    if not isinstance(poll, str) or not poll.startswith("/api/jobs/"):
        poll = "/api/jobs/" + job_id.strip()
    return job_id.strip(), poll


def _unwrap_done(obj):
    if (isinstance(obj, dict) and obj.get("state") == "done" and isinstance(obj.get("result"), dict)
            and "mood" not in obj and "bots" not in obj and "all_time" not in obj):
        return obj["result"]
    return obj


def _poll(job_id, path):
    deadline = time.monotonic() + _job_wait()
    while True:
        outcome = _http_json("GET", path)
        if outcome[0] == "err":
            err = outcome[1]
            if err.get("http_status") == 404:
                return ("err", _fail("job_lost", f"Job {job_id} disappeared.", job_id=job_id))
            return outcome
        obj = outcome[2]
        if not obj:
            return ("err", _fail("job_lost", f"Job {job_id} disappeared.", job_id=job_id))
        state = obj.get("state")
        if state == "done":
            result = obj.get("result")
            if not isinstance(result, dict):
                return ("err", _fail("bad_response", f"Job {job_id} finished without a result.", job_id=job_id))
            return ("ok", 200, result)
        if state == "error":
            message = obj.get("error") if isinstance(obj.get("error"), str) and obj["error"].strip() else "Job failed."
            extra = {"job_id": job_id}
            if obj.get("error_kind"):
                extra["server_error_kind"] = obj["error_kind"]
            return ("err", _fail("job_error", message, **extra))
        if time.monotonic() >= deadline:
            return ("err", _fail(
                "still_running",
                f"Job {job_id} is still running.",
                status="still_running",
                job_id=job_id,
                poll=path,
            ))
        time.sleep(min(_poll_interval(), max(0.0, deadline - time.monotonic())))


def _call(method, path, body, present):
    outcome = _http_json(method, path, body)
    if outcome[0] == "err":
        return outcome[1]
    obj = outcome[2]
    ticket = _ticket(obj)
    if ticket:
        outcome = _poll(*ticket)
        if outcome[0] == "err":
            return outcome[1]
        obj = outcome[2]
    else:
        obj = _unwrap_done(obj)
    return present(obj)


def _present_object(obj, what):
    if not isinstance(obj, dict):
        return _fail("bad_response", f"{what} was not a JSON object.")
    return obj


def _present_mood(obj):
    if not isinstance(obj, dict):
        return _fail("bad_response", "Mood response was not a JSON object.")
    mood = dict(obj)
    mood.pop("_meta", None)
    if "musical_attributes" not in mood and "page_summary" not in mood:
        return _fail("bad_response", "Mood response was missing the analysis.")
    return mood


def _present_synth(obj, source=None):
    if not isinstance(obj, dict):
        return _fail("bad_response", "Synthesis result was not a JSON object.")
    mood = obj.get("mood")
    track = obj.get("track") if isinstance(obj.get("track"), dict) else {}
    if not isinstance(mood, dict) or not track.get("path"):
        return _fail("bad_response", "Synthesis result had no track.")
    rel = track.get("url") if isinstance(track.get("url"), str) else ""
    if rel.startswith("http://") or rel.startswith("https://"):
        track_url = rel
    else:
        if rel and not rel.startswith("/"):
            rel = "/" + rel
        track_url = _base() + rel
    out = dict(mood)
    out["track_path"] = track["path"]
    out["track_file"] = track.get("file")
    out["track_url"] = track_url
    out["timing"] = obj.get("timing") if isinstance(obj.get("timing"), dict) else {}
    if obj.get("vocals"):
        out["vocals"] = obj["vocals"]
    topic = obj.get("topic")
    if isinstance(topic, dict):
        requested = topic.get("requested_source") or source or ""
        out["topic"] = topic.get("topic") or ""
        out["note"] = topic.get("note") or ""
        out["requested_source"] = requested
        if topic.get("source") == "web" or requested == "web":
            pages = topic.get("pages") or []
            out["source"] = "web"
            out["pages"] = pages
            out["page_count"] = topic.get("page_count") or len(pages)
        else:
            out["what_people_are_saying"] = mood.get("page_summary")
            out["post_count"] = topic.get("post_count")
            out["post_source"] = topic.get("source")
            out["x_search_url"] = topic.get("x_search_url")
            out["source"] = requested or "x"
    return out


def _topic_result(result):
    if not isinstance(result, dict) or result.get("ok") is not False:
        return result
    kind = result.get("server_error_kind")
    status = result.get("http_status")
    if kind in _TOPIC_KINDS or status == 422 or (status == 404 and result.get("error_kind") == "http_error"):
        raise ToolError(result.get("error") or "Topic request failed.")
    return result


def _norm_flags(force, vocals, script, agent_id):
    if not isinstance(force, bool):
        return _fail("bad_args", "force must be true or false."), None, None, None
    if vocals is None:
        vocals = "none"
    if not isinstance(vocals, str):
        return _fail("bad_args", "vocals must be a string."), None, None, None
    if script is None:
        script = ""
    if not isinstance(script, str):
        return _fail("bad_args", "script must be a string."), None, None, None
    if agent_id is None:
        agent_id = ""
    if not isinstance(agent_id, str):
        return _fail("bad_args", "agent_id must be a string."), None, None, None
    return None, vocals, script, agent_id


def _require_url(url):
    if not isinstance(url, str) or not url.strip():
        return _fail("bad_args", "url is required.")
    return None


def _norm_topic(topic, posts, source):
    if source is None:
        source = "web"
    if not isinstance(source, str):
        raise ToolError(_BAD_SOURCE)
    source_name = source.strip().lower()
    if source_name not in ("", "web", "x"):
        raise ToolError(_BAD_SOURCE)
    source_name = source_name or "web"
    if posts is not None and (not isinstance(posts, list) or not all(isinstance(item, str) for item in posts)):
        return _fail("bad_args", "posts must be a list of strings."), source_name, None
    if not isinstance(topic, str):
        raise ToolError(_EMPTY_TOPIC)
    cleaned = re.sub(r"\s+", " ", topic).strip()
    if len(cleaned) > 120:
        cleaned = cleaned[:120].rstrip()
    if source_name == "x" and posts and not cleaned:
        return None, source_name, cleaned
    if not cleaned or not any(ch.isalnum() for ch in cleaned):
        raise ToolError(_EMPTY_TOPIC)
    return None, source_name, cleaned


def _norm_report(ident, state, task, name, owner, role, progress, artifact, error, agent_id):
    if not isinstance(ident, str) or ident.strip() == "index" or not _ID_RE.match(ident.strip() if isinstance(ident, str) else ""):
        return _fail("bad_args", "id must be lowercase letters, digits, '.', '_' or '-' (max 64).")
    if not isinstance(state, str) or state.strip() not in _STATES:
        return _fail("bad_args", "state must be running, idle, done, or error.")
    if not isinstance(task, str) or not task.strip():
        return _fail("bad_args", "task is required.")
    fields = {}
    for field, value in (("name", name), ("owner", owner), ("role", role), ("artifact", artifact),
                         ("error", error), ("agent_id", agent_id)):
        if value is None:
            value = ""
        if not isinstance(value, str):
            return _fail("bad_args", f"{field} must be a string.")
        fields[field] = value
    if isinstance(progress, bool) or not isinstance(progress, int) or progress < -1 or progress > 100:
        return _fail("bad_args", "progress must be an integer from 0 to 100.")
    fields["id"] = ident.strip()
    fields["state"] = state.strip()
    fields["task"] = task
    fields["progress"] = progress
    return fields


def _bounded(fn):
    box = {}

    def work():
        try:
            box["value"] = fn()
        except Exception as exc:
            box["error"] = exc

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(_job_wait())
    if thread.is_alive():
        job_id = uuid.uuid4().hex[:12]
        return _fail("still_running", f"Job {job_id} is still running.", status="still_running", job_id=job_id)
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _from_exception(exc, topic=False):
    name = type(exc).__name__
    if topic and name in ("TopicNotFound", "EmptyTopic", "BadSource", "IngestError"):
        raise ToolError(str(exc))
    if name == "AgentError":
        return _fail("bad_args", str(exc))
    if name == "JSONDecodeError":
        return _fail("bad_response", "Response was not JSON.")
    if name in ("TimeoutError",) or isinstance(exc, (TimeoutError, socket.timeout)):
        return _fail("timeout", "The request timed out.")
    if name in ("HTTPError", "IngestError"):
        return _fail("http_error", str(exc))
    return _fail("internal", f"{name}: {exc}")


def _guard(fn, topic=False):
    try:
        return _bounded(fn)
    except ToolError:
        raise
    except Exception as exc:
        return _from_exception(exc, topic=topic)


def _dispatch(local_fn, http_fn):
    if _use_http():
        return http_fn()
    return local_fn()


def _http_synthesize_url(url, force, vocals, script, agent_id):
    body = {"url": url.strip(), "force": force, "vocals": vocals or "none", "async": True}
    if script:
        body["script"] = script
    if str(agent_id or "").strip():
        body["agent_id"] = agent_id.strip()
    return _call("POST", "/api/synthesize", body, _present_synth)


def _http_mood(url, agent_id):
    query = {"url": url.strip()}
    if str(agent_id or "").strip():
        query["agent_id"] = agent_id.strip()
    return _call("GET", "/api/mood?" + urllib.parse.urlencode(query), None, _present_mood)


def _http_topic(topic, posts, force, source, vocals, script, agent_id):
    body = {"topic": topic, "force": force, "source": source, "vocals": vocals or "none", "async": True}
    if posts:
        body["posts"] = posts
    if script:
        body["script"] = script
    if str(agent_id or "").strip():
        body["agent_id"] = agent_id.strip()
    return _topic_result(_call("POST", "/api/synthesize_topic", body, lambda obj: _present_synth(obj, source)))


def _http_report(fields):
    body = {"id": fields["id"], "state": fields["state"], "task": fields["task"]}
    for key in ("name", "owner", "role", "artifact", "error"):
        if fields.get(key):
            body[key] = fields[key]
    if fields["progress"] >= 0:
        body["progress"] = fields["progress"]
    if fields.get("agent_id", "").strip():
        body["agent_id"] = fields["agent_id"].strip()
    return _call("POST", "/api/agents", body, lambda obj: _present_object(obj, "Agent status"))


def _local_synthesize_url(url, force, vocals, script, agent_id):
    _agents, pipeline, _usage = _deps()
    who, started = _who(agent_id), time.perf_counter()
    try:
        result = pipeline.synthesize(url, force=force, vocals=vocals or "none", script=script or None,
                                     agent=agent_id or "anonymous")
    except Exception as exc:
        _note(who, "synthesize", started, ok=False, error=str(exc))
        raise
    timing = result.get("timing") or {}
    audio = float((result.get("track") or {}).get("duration_s") or 0)
    cached = bool(timing.get("track_cached"))
    _note(who, "synthesize", started, ok=True, audio_s=audio, tracks=0 if cached or not audio else 1)
    out = dict(result["mood"], track_path=result["track"]["path"], track_file=result["track"]["file"],
               track_url=_base() + result["track"]["url"], timing=result["timing"])
    if result.get("vocals"):
        out["vocals"] = result["vocals"]
    return out


def _local_mood(url, agent_id):
    who, started = _who(agent_id), time.perf_counter()
    _agents, pipeline, _usage = _deps()
    try:
        mood, _debug, _page, _timing = pipeline.mood_for(url)
    except Exception as exc:
        _note(who, "get_mood", started, ok=False, error=str(exc))
        raise
    _note(who, "get_mood", started, ok=True)
    return mood


def _local_topic(topic, posts, force, source, vocals, script, agent_id):
    from engine.ingest import IngestError
    from engine.topic_web import BadSource, EmptyTopic
    _agents, pipeline, _usage = _deps()
    who, started = _who(agent_id), time.perf_counter()
    try:
        result = pipeline.synthesize_topic(topic, posts=posts, force=force, source=source,
                                           vocals=vocals or "none", script=script or None,
                                           agent=agent_id or "anonymous")
    except Exception as exc:
        _note(who, "synthesize_topic", started, ok=False, error=str(exc))
        if isinstance(exc, (pipeline.TopicNotFound, IngestError, EmptyTopic, BadSource)):
            raise ToolError(str(exc))
        raise
    timing = result.get("timing") or {}
    audio = float((result.get("track") or {}).get("duration_s") or 0)
    cached = bool(timing.get("track_cached"))
    topic_info = result["topic"]
    reads = int(topic_info.get("x_reads") or 0)
    _note(who, "synthesize_topic", started, ok=True, audio_s=audio,
          tracks=0 if cached or not audio else 1, x_reads=reads)
    out = dict(result["mood"], topic=topic_info["topic"], note=topic_info.get("note") or "",
               requested_source=topic_info.get("requested_source") or source,
               track_path=result["track"]["path"], track_file=result["track"]["file"],
               track_url=_base() + result["track"]["url"], timing=result["timing"])
    if result.get("vocals"):
        out["vocals"] = result["vocals"]
    if topic_info.get("source") == "web" or topic_info.get("requested_source") == "web":
        pages = topic_info.get("pages") or []
        out.update(source="web", pages=pages, page_count=topic_info.get("page_count") or len(pages))
        return out
    out.update(what_people_are_saying=result["mood"]["page_summary"], post_count=topic_info["post_count"],
               post_source=topic_info["source"], x_search_url=topic_info["x_search_url"],
               source=topic_info.get("requested_source") or "x")
    return out


def _local_report(fields):
    agents, _pipeline, _usage = _deps()
    body = {"id": fields["id"], "state": fields["state"], "task": fields["task"]}
    for key in ("name", "owner", "role", "artifact", "error"):
        if fields.get(key):
            body[key] = fields[key]
    if fields["progress"] >= 0:
        body["progress"] = fields["progress"]
    agent_id = fields.get("agent_id") or ""
    who = _who(agent_id) if str(agent_id).strip() else _who(fields["id"])
    started = time.perf_counter()
    try:
        rec = agents.report(body)
    except Exception as exc:
        _note(who, "report_status", started, ok=False, error=str(exc))
        raise
    _note(who, "report_status", started, ok=True)
    return rec


def _local_list():
    agents, _pipeline, _usage = _deps()
    return agents.list_agents()


def _local_usage():
    _agents, _pipeline, usage = _deps()
    return usage.summary()


async def _go(fn):
    try:
        return await anyio.to_thread.run_sync(fn)
    except ToolError:
        raise
    except Exception as exc:
        return _fail("internal", f"{type(exc).__name__}: {exc}")


server = MCPServer(
    name="url-to-lofi",
    title="URL-to-Lofi Synthesizer",
    version="0.1.0",
    instructions="Turn any public web page into an original lofi track. get_mood_analysis returns the mood JSON; "
                 "synthesize_url also renders an mp3 and returns its path and URL.",
)


@server.tool(description="Fetch a public web page, analyze its mood deterministically and render an original ~44 s lofi "
                         "track. Returns {url, page_summary, sentiment, lofi_prompt, musical_attributes, cover_art_prompt} "
                         "plus track_path, track_url and timing.")
async def synthesize_url(url: str, force: bool = False, vocals: str = "none", script: str = "",
                        agent_id: str = "") -> dict:
    problem = _require_url(url)
    if problem:
        return problem
    problem, vocals, script, agent_id = _norm_flags(force, vocals, script, agent_id)
    if problem:
        return problem

    def run():
        return _dispatch(
            lambda: _guard(lambda: _local_synthesize_url(url, force, vocals, script, agent_id)),
            lambda: _http_synthesize_url(url, force, vocals, script, agent_id),
        )

    return await _go(run)


@server.tool(description="Fetch a public web page and return its lofi mood analysis (PRD schema: url, page_summary, "
                         "sentiment, lofi_prompt, musical_attributes{bpm,key,primary_instruments,mood_tags}, cover_art_prompt). "
                         "No audio is rendered.")
async def get_mood_analysis(url: str, agent_id: str = "") -> dict:
    problem = _require_url(url)
    if problem:
        return problem
    problem, _vocals, _script, agent_id = _norm_flags(False, "none", "", agent_id)
    if problem:
        return problem

    def run():
        return _dispatch(
            lambda: _guard(lambda: _local_mood(url, agent_id)),
            lambda: _http_mood(url, agent_id),
        )

    return await _go(run)


# --- Learn a topic, via X ------------------------------------------------------
# synthesize_topic. Kept apart from the URL tools and the live-agent tools.
# source="web" (default) reads public pages; source="x" is the X-posts path above.

@server.tool(description="Make an original ~44 s lofi track from a topic. source is 'web' (default) or 'x'. "
                         "Web finds public pages (Wikipedia, news RSS, Hacker News), reads them, and returns pages "
                         "as {title, url, via, method, words}. X uses recent public posts: pass posts yourself, or "
                         "omit them to try the X API (X_BEARER_TOKEN) and then x_topics.json. An empty topic or a "
                         "source other than web or x is an error you can show the user. Returns the PRD mood JSON "
                         "plus note, track_path and track_url. X also returns what_people_are_saying, post_count, "
                         "post_source and x_search_url. vocals defaults to none. agent_id credits the usage log.")
async def synthesize_topic(topic: str, posts: Optional[list[str]] = None, force: bool = False,
                           source: str = "web", vocals: str = "none", script: str = "",
                           agent_id: str = "") -> dict:
    problem, vocals, script, agent_id = _norm_flags(force, vocals, script, agent_id)
    if problem:
        return problem
    problem, source, topic = _norm_topic(topic, posts, source)
    if problem:
        return problem

    def run():
        return _dispatch(
            lambda: _guard(lambda: _local_topic(topic, posts, force, source, vocals, script, agent_id), topic=True),
            lambda: _http_topic(topic, posts, force, source, vocals, script, agent_id),
        )

    return await _go(run)


# --- Live agents + usage -------------------------------------------------------
# report_status, list_agents, and get_usage. Kept in one block so other agents
# can merge the synthesize tools above without touching this section.

@server.tool(description="Register this agent on the Lofi control room, or update what it is doing. "
                         "First call creates status/<id>.json. Later calls merge. "
                         "id is lowercase letters, digits, '.', '_' or '-' (max 64). "
                         "state is running, idle, done, or error. owner and task are required the first time. "
                         "Optional agent_id credits the usage log; otherwise the status id is used.")
async def report_status(id: str, state: str, task: str, name: str = "", owner: str = "", role: str = "",
                        progress: int = -1, artifact: str = "", error: str = "", agent_id: str = "") -> dict:
    fields = _norm_report(id, state, task, name, owner, role, progress, artifact, error, agent_id)
    if fields.get("ok") is False:
        return fields

    def run():
        return _dispatch(
            lambda: _guard(lambda: _local_report(fields)),
            lambda: _http_report(fields),
        )

    return await _go(run)


@server.tool(description="List agents the control room knows about. running/idle agents with no update for "
                         "stale_after_s seconds are presence=offline. done and error stay settled. Files are not deleted.")
async def list_agents() -> dict:
    def run():
        return _dispatch(
            lambda: _guard(_local_list),
            lambda: _call("GET", "/api/agents", None, lambda obj: _present_object(obj, "Agent list")),
        )

    return await _go(run)


@server.tool(description="Read the local usage meter: calls, errors, tracks made, audio minutes, and X reads "
                         "today and all-time, plus a 24-hour hourly histogram. Nothing is sent off the machine. "
                         "X reads count only when a tool records them (tool name x_search or field x_reads).")
async def get_usage() -> dict:
    def run():
        return _dispatch(
            lambda: _guard(_local_usage),
            lambda: _call("GET", "/api/usage", None, lambda obj: _present_object(obj, "Usage")),
        )

    return await _go(run)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=53350)
    a = ap.parse_args()
    if a.http:
        server.run("streamable-http", host=a.host, port=a.port)
    else:
        server.run()
