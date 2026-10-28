# URL-to-Lofi MCP server

Tools:
- `get_mood_analysis(url, agent_id="")` → PRD mood JSON: `{url, page_summary, sentiment, lofi_prompt, musical_attributes{bpm, key, primary_instruments[], mood_tags[]}, cover_art_prompt}`
- `synthesize_url(url, force=false, vocals="none", script="", agent_id="")` → the same JSON, plus `track_path` (absolute path on the box), `track_file` (relative), `track_url` (served by the dashboard) and `timing`. `vocals` stays `none` unless the caller asks for `summary` or `song` ([docs/VOCALS.md](../docs/VOCALS.md)).
- `synthesize_topic(topic, posts=None, force=false, source="web", vocals="none", script="", agent_id="")` → a track from a topic. `source` is `"web"` (default) or `"x"`. Web returns `pages` (title, url, via, method, words) plus the PRD mood JSON. X returns `what_people_are_saying`, `post_count`, `post_source`, and `x_search_url`; pass `posts` to skip search. An empty topic, a bad source, or an unknown topic raises a message you can show the user. Live X searches (`X_BEARER_TOKEN`) add X reads to the usage meter. Example: `synthesize_topic(topic="lofi")` or `synthesize_topic(topic="SpaceX", source="x")`. `vocals` defaults to `none`.
- `report_status(id, state, task, name="", owner="", role="", progress=-1, artifact="", error="", agent_id="")` → create or update one agent. Example: `report_status(id="alex-news", owner="Alex", state="running", task="Pulling headlines")`
- `list_agents()` → the same list as `GET /api/agents`, including `presence` and `stale_after_s`.
- `get_usage()` → local usage totals (today, all-time, per agent, last 24 hours, X reads against the free-tier budget). No network.

`agent_id` credits the usage log. Leave it blank and synthesize/mood calls are `anonymous`; `report_status` credits the status id.

## Errors

`LOFI_MCP_TRANSPORT` chooses where the tools run.

- `auto` (default) calls the app server when `GET /api/health` on `LOFI_PUBLIC_BASE` returns `{"ok": true}`. If that server is down, the tool runs the engine in this process.
- `http` always calls the app server and does not fall back.
- `local` always runs the engine in this process.

A transport failure is a dict, not a crash:

```json
{"ok": false, "error_kind": "server_down", "error": "The app server is unreachable."}
```

`error_kind` is `bad_args`, `server_down`, `timeout`, `http_error`, `bad_response`, `still_running`, `job_lost`, `job_error`, or `internal`. `http_error` includes `http_status`. `still_running` includes `job_id`, `status`, and `poll` once `LOFI_MCP_JOB_WAIT` seconds have passed (default 180), so a long render returns instead of hanging. One HTTP call is bounded by `LOFI_MCP_HTTP_TIMEOUT` (default 15 seconds). A bad value for either setting falls back to the default.

An empty topic or a source other than `web` or `x` still raises a message you can show the user. So does an unknown topic.

It renders an original ~42-44 s lofi mp3 in about 2-4 s. It also reports progress to the dashboard as bot `url-lofi`.

## stdio (a bot launches it)
```json
{
  "mcpServers": {
    "url-to-lofi": {
      "command": "python3",
      "args": ["mcp/lofi_mcp_server.py"],
      "env": { "LOFI_PUBLIC_BASE": "http://127.0.0.1:53343" }
    }
  }
}
```

## Streamable HTTP (one shared server)
```bash
python3 mcp/lofi_mcp_server.py --http --port 53350   # endpoint: http://127.0.0.1:53350/mcp
```
```json
{ "mcpServers": { "url-to-lofi": { "url": "http://127.0.0.1:53350/mcp" } } }
```
It binds to localhost only. To let teammates' bots reach it, put it behind an authenticated tunnel or host it. It has no auth of its own.

## Test
```bash
python3 mcp/test_mcp_client.py https://example.com                               # stdio
python3 mcp/test_mcp_client.py https://example.com http://127.0.0.1:53350/mcp     # HTTP
python3 -m unittest tests.test_mcp_robustness                                     # offline, no engine
```
Uses SDK v2 (`mcp>=2.3`, `from mcp.server.mcpserver import MCPServer`). On `mcp<2` the same code works with `FastMCP` from `mcp.server.fastmcp`.
