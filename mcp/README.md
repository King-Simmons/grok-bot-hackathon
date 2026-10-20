# URL-to-Lofi MCP server

Tools:
- `get_mood_analysis(url)` → PRD mood JSON: `{url, page_summary, sentiment, lofi_prompt, musical_attributes{bpm, key, primary_instruments[], mood_tags[]}, cover_art_prompt}`
- `synthesize_url(url, force=false)` → the same JSON, plus `track_path` (absolute path on the box), `track_file` (relative), `track_url` (served by the dashboard) and `timing`.
- `speak_text(text, exaggeration=null, cfg_weight=null, speed=null, lofi=false, preset=null, out_path=null)` → one spoken clip from `audio.voice.synthesize_speech` (built-in Chatterbox voice, no cloning). `preset` is `news` (0.7 / 0.3 / speed 0.9) or `dj` (0.5 / 0.5 / speed 0.9). Returns `path`, `url`, and the rest of the clip dict, or `{error, chatterbox: false}` when chatterbox-tts is not installed. Music tools keep working. See [docs/VOICE.md](../docs/VOICE.md).

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
