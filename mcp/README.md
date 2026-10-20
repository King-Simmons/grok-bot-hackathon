# URL-to-Lofi MCP server

Tools:
- `get_mood_analysis(url, agent_id="")` → PRD mood JSON: `{url, page_summary, sentiment, lofi_prompt, musical_attributes{bpm, key, primary_instruments[], mood_tags[]}, cover_art_prompt}`
- `synthesize_url(url, force=false, agent_id="")` → the same JSON, plus `track_path` (absolute path on the box), `track_file` (relative), `track_url` (served by the dashboard) and `timing`.
- `synthesize_topic(topic, posts=None, force=false, agent_id="")` → a track whose mood comes from recent public X posts. Example: `synthesize_topic(topic="SpaceX")`. Pass `posts` to skip search. Unknown topics raise a message you can show the user. Live X searches (`X_BEARER_TOKEN`) add X reads to the usage meter.
- `report_status(id, state, task, name="", owner="", role="", progress=-1, artifact="", error="", agent_id="")` → create or update one agent. Example: `report_status(id="alex-news", owner="Alex", state="running", task="Pulling headlines")`
- `list_agents()` → the same list as `GET /api/agents`, including `presence` and `stale_after_s`.
- `get_usage()` → local usage totals (today, all-time, per agent, last 24 hours, X reads against the free-tier budget). No network.

`agent_id` credits the usage log. Leave it blank and synthesize/mood calls are `anonymous`; `report_status` credits the status id.

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
```
Uses SDK v2 (`mcp>=2.3`, `from mcp.server.mcpserver import MCPServer`). On `mcp<2` the same code works with `FastMCP` from `mcp.server.fastmcp`.
