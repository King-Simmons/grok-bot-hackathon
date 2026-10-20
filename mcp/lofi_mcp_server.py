"""mcp/lofi_mcp_server.py - MCP server for the URL-to-Lofi Synthesizer (python `mcp` SDK v2).

Tools:
  synthesize_url(url, force=False, agent_id="") -> PRD mood JSON + track file path/URL
  get_mood_analysis(url, agent_id="")           -> PRD mood JSON only (no audio)
  synthesize_topic(topic, posts=None, force=False, source="web", agent_id="")
                                                -> lofi from public web pages (default) or X posts (source="x")
  report_status(...)                            -> register or update one agent on the dashboard
  list_agents()                                 -> agents currently on disk, with online/offline
  get_usage()                                   -> local usage totals (no network)

Transports:
  stdio (default):        python3 mcp/lofi_mcp_server.py
  streamable HTTP:        python3 mcp/lofi_mcp_server.py --http --port 53350   (endpoint /mcp)
Env: LOFI_PUBLIC_BASE (default http://127.0.0.1:53343) is prefixed to track URLs.
agent_id, when passed, is who the usage log credits. Omit it and the credit is "anonymous".
The live-agent tools are the marked section at the bottom of this file.
"""
import argparse, os, sys, time
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if sys.path and os.path.abspath(sys.path[0]) == os.path.dirname(os.path.abspath(__file__)):
    sys.path.pop(0)  # don't let this folder (named "mcp") shadow the mcp package
sys.path.insert(0, ROOT)

import anyio  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from engine import agents, pipeline, usage  # noqa: E402
from engine.ingest import IngestError  # noqa: E402

BASE = os.environ.get("LOFI_PUBLIC_BASE", "http://127.0.0.1:53343").rstrip("/")


def _who(agent_id):
    return usage.caller({"agent_id": agent_id or ""})


def _note(who, tool, started, *, ok, error="", audio_s=0.0, tracks=0, x_reads=0):
    usage.record(who, tool, ok, time.perf_counter() - started, audio_s=audio_s, tracks=tracks,
                 x_reads=x_reads, error=(error or "")[:200])

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
async def synthesize_url(url: str, force: bool = False, agent_id: str = "") -> dict:
    who, started = _who(agent_id), time.perf_counter()
    try:
        r = await anyio.to_thread.run_sync(lambda: pipeline.synthesize(url, force=force))
    except Exception as e:
        _note(who, "synthesize", started, ok=False, error=str(e))
        raise
    timing = r.get("timing") or {}
    audio = float((r.get("track") or {}).get("duration_s") or 0)
    cached = bool(timing.get("track_cached"))
    _note(who, "synthesize", started, ok=True, audio_s=audio, tracks=0 if cached or not audio else 1)
    return dict(r["mood"], track_path=r["track"]["path"], track_file=r["track"]["file"],
                track_url=BASE + r["track"]["url"], timing=r["timing"])


@server.tool(description="Fetch a public web page and return its lofi mood analysis (PRD schema: url, page_summary, "
                         "sentiment, lofi_prompt, musical_attributes{bpm,key,primary_instruments,mood_tags}, cover_art_prompt). "
                         "No audio is rendered.")
async def get_mood_analysis(url: str, agent_id: str = "") -> dict:
    who, started = _who(agent_id), time.perf_counter()
    try:
        mood, _debug, _page, _t = await anyio.to_thread.run_sync(lambda: pipeline.mood_for(url))
    except Exception as e:
        _note(who, "get_mood", started, ok=False, error=str(e))
        raise
    _note(who, "get_mood", started, ok=True)
    return mood


# --- Learn a topic, via X ------------------------------------------------------
# synthesize_topic. Kept apart from the URL tools and the live-agent tools.
# source="web" (default) reads public pages; source="x" is the X-posts path above.

@server.tool(description="Make an original ~44 s lofi track from a topic. source is 'web' (default) or 'x'. "
                         "Web finds public pages (Wikipedia, news RSS, Hacker News), reads them, and returns pages "
                         "as {title, url, via, method, words}. X uses recent public posts: pass posts yourself, or "
                         "omit them to try the X API (X_BEARER_TOKEN) and then x_topics.json. An empty topic or a "
                         "source other than web or x is an error you can show the user. Returns the PRD mood JSON "
                         "plus note, track_path and track_url. X also returns what_people_are_saying, post_count, "
                         "post_source and x_search_url. agent_id credits the usage log.")
async def synthesize_topic(topic: str, posts: Optional[list[str]] = None, force: bool = False,
                           source: str = "web", agent_id: str = "") -> dict:
    from mcp.server.mcpserver.exceptions import ToolError
    from engine.topic_web import BadSource, EmptyTopic
    who, started = _who(agent_id), time.perf_counter()
    try:
        r = await anyio.to_thread.run_sync(
            lambda: pipeline.synthesize_topic(topic, posts=posts, force=force, source=source))
    except Exception as e:
        _note(who, "synthesize_topic", started, ok=False, error=str(e))
        if isinstance(e, (pipeline.TopicNotFound, IngestError, EmptyTopic, BadSource)):
            raise ToolError(str(e))
        raise
    timing = r.get("timing") or {}
    audio = float((r.get("track") or {}).get("duration_s") or 0)
    cached = bool(timing.get("track_cached"))
    t = r["topic"]
    reads = int(t.get("x_reads") or 0)
    _note(who, "synthesize_topic", started, ok=True, audio_s=audio, tracks=0 if cached or not audio else 1, x_reads=reads)
    out = dict(r["mood"], topic=t["topic"], note=t.get("note") or "",
               requested_source=t.get("requested_source") or source,
               track_path=r["track"]["path"], track_file=r["track"]["file"],
               track_url=BASE + r["track"]["url"], timing=r["timing"])
    if t.get("source") == "web" or t.get("requested_source") == "web":
        pages = t.get("pages") or []
        out.update(source="web", pages=pages, page_count=t.get("page_count") or len(pages))
        return out
    out.update(what_people_are_saying=r["mood"]["page_summary"], post_count=t["post_count"],
               post_source=t["source"], x_search_url=t["x_search_url"], source=t.get("requested_source") or "x")
    return out


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
    body = {"id": id, "state": state, "task": task}
    if name: body["name"] = name
    if owner: body["owner"] = owner
    if role: body["role"] = role
    if artifact: body["artifact"] = artifact
    if error: body["error"] = error
    if progress >= 0: body["progress"] = progress
    who = _who(agent_id) if str(agent_id or "").strip() else _who(id)
    started = time.perf_counter()
    try:
        rec = await anyio.to_thread.run_sync(lambda: agents.report(body))
    except Exception as e:
        _note(who, "report_status", started, ok=False, error=str(e))
        raise
    _note(who, "report_status", started, ok=True)
    return rec


@server.tool(description="List agents the control room knows about. running/idle agents with no update for "
                         "stale_after_s seconds are presence=offline. done and error stay settled. Files are not deleted.")
async def list_agents() -> dict:
    return await anyio.to_thread.run_sync(agents.list_agents)


@server.tool(description="Read the local usage meter: calls, errors, tracks made, audio minutes, and X reads "
                         "today and all-time, plus a 24-hour hourly histogram. Nothing is sent off the machine. "
                         "X reads count only when a tool records them (tool name x_search or field x_reads).")
async def get_usage() -> dict:
    return await anyio.to_thread.run_sync(usage.summary)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=53350)
    a = ap.parse_args()
    if a.http:
        server.run("streamable-http", host=a.host, port=a.port)
    else:
        server.run()
