"""mcp/lofi_mcp_server.py - MCP server for the URL-to-Lofi Synthesizer (python `mcp` SDK v2).

Tools:
  synthesize_url(url, force=False) -> PRD mood JSON + track file path/URL (renders an original ~44 s lofi mp3)
  get_mood_analysis(url)           -> PRD mood JSON only (no audio)

Transports:
  stdio (default):        python3 mcp/lofi_mcp_server.py
  streamable HTTP:        python3 mcp/lofi_mcp_server.py --http --port 53350   (endpoint /mcp)
Env: LOFI_PUBLIC_BASE (default http://127.0.0.1:53343) is prefixed to track URLs.
"""
import argparse, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if sys.path and os.path.abspath(sys.path[0]) == os.path.dirname(os.path.abspath(__file__)):
    sys.path.pop(0)  # don't let this folder (named "mcp") shadow the mcp package
sys.path.insert(0, ROOT)

import anyio  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from engine import pipeline  # noqa: E402

BASE = os.environ.get("LOFI_PUBLIC_BASE", "http://127.0.0.1:53343").rstrip("/")

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
async def synthesize_url(url: str, force: bool = False) -> dict:
    r = await anyio.to_thread.run_sync(lambda: pipeline.synthesize(url, force=force))
    return dict(r["mood"], track_path=r["track"]["path"], track_file=r["track"]["file"],
                track_url=BASE + r["track"]["url"], timing=r["timing"])


@server.tool(description="Fetch a public web page and return its lofi mood analysis (PRD schema: url, page_summary, "
                         "sentiment, lofi_prompt, musical_attributes{bpm,key,primary_instruments,mood_tags}, cover_art_prompt). "
                         "No audio is rendered.")
async def get_mood_analysis(url: str) -> dict:
    mood, _debug, _page, _t = await anyio.to_thread.run_sync(lambda: pipeline.mood_for(url))
    return mood


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=53350)
    a = ap.parse_args()
    if a.http:
        server.run("streamable-http", host=a.host, port=a.port)
    else:
        server.run()
