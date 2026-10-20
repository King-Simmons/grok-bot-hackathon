# Lofi News Radio / URL-to-Lofi Synthesizer

A Grok Bot hackathon entry for **Track 2: Build for the Bot Economy**.

Lofi News Radio is a control room for a small crew of bots, plus a URL-to-lofi engine. Paste a public link. The engine reads the page, scores a mood deterministically (no LLM), and renders an original lofi track. Other bots can call the same pipeline through MCP.

![Lofi News Radio dashboard](docs/dashboard.png)

*Control room, Oct 19, 2026, 6:56 PM CT.*

Built by King Simmons with his Grok Bot, Zlatan. PRD by Jonathan Chen, *URL-to-Lofi Synthesizer*. The team PRD and research notes live in the team's Google Drive folder.

## Repo layout

| path | what |
|---|---|
| `dashboard/index.html` | Control room: clock, CSS lofi scene, player, Web Audio mixer, bot panel, paste-a-link box |
| `dashboard/botstatus.py` | Stdlib status CLI. Writes repo-root `status/` and `dashboard/tracks.json` |
| `dashboard/status` | Symlink to `../status`, so the page's relative `status/index.json` fetch works |
| `dashboard/*.json` | Sample headlines, inbox, and episode data |
| `engine/` | URL fetch, mood JSON, code-synthesized beat, local HTTP API |
| `mcp/` | MCP server: `synthesize_url`, `get_mood_analysis` |
| `audio/` | Original beat generator, plot, and vocal lofi chain |
| `samples/beat-001.mp3` | One original code-synthesized demo beat |
| `docs/` | Status protocol and the dashboard screenshot |
| `status/` | One JSON file per bot. `example-zlatan.json` is the sample check-in |
| `tracks/` | URL-generated mp3s (gitignored) plus a JSON sidecar |
| `run.sh` | Creates `./.venv` on first run, then serves the repo root |

The page loads `headlines.json`, `inbox.json`, `episode.json`, `tracks.json`, and `status/index.json` relative to `dashboard/index.html`. The engine serves the **repository root**, so the paste-a-link box calls `/api/synthesize` and plays tracks from `/tracks/…`.

## Quick start

Python 3.10+. From the repository root:

```bash
./run.sh
./run.sh --mcp
```

The first run creates `./.venv`, installs [requirements.txt](requirements.txt), and exports `SSL_CERT_FILE` from certifi so HTTPS works on python.org macOS Python.

- Control room: <http://127.0.0.1:53343/dashboard/>
- API: <http://127.0.0.1:53343/api/health>
- With `--mcp`: streamable HTTP on <http://127.0.0.1:53350/mcp>

`?link=` pre-fills the paste box, for example <http://127.0.0.1:53343/dashboard/?link=https://example.com>.

You can also serve the static page alone:

```bash
python3 dashboard/botstatus.py refresh
python3 -m http.server 8000
```

Open <http://127.0.0.1:8000/dashboard/>. The paste-a-link box needs `./run.sh`, because that is what serves `/api/*`.

### Bot status

```bash
python3 dashboard/botstatus.py set \
  --id king-zlatan --name Zlatan --owner King --role "main assistant" \
  --state running --task "Coordinating hackathon build"
python3 dashboard/botstatus.py list
```

See [docs/STATUS_PROTOCOL.md](docs/STATUS_PROTOCOL.md).

### HTTP API

```
GET  /api/health
GET  /api/mood?url=https://example.com
POST /api/synthesize {"url": "https://example.com"}
POST /api/synthesize {"url": "https://example.com", "async": true}
```

`force: true` re-renders even when that track already exists. Async jobs are polled at `GET /api/jobs/<id>`.

### MCP

See [mcp/README.md](mcp/README.md). From the repo root, after `./run.sh` has created the venv:

```bash
.venv/bin/python mcp/test_mcp_client.py https://example.com
```

## Dependencies

`./run.sh` installs [requirements.txt](requirements.txt): numpy, scipy, certifi, and optional pyloudnorm, lameenc, trafilatura, and vaderSentiment. `mcp` is required for the MCP server.

`ffmpeg` on `PATH` is the fallback MP3 encoder and is used by `audio/lofi_vocals.py`. `demucs` is optional and is not installed by `requirements.txt`. Only pass `--separate` when you have installed it yourself, and only on audio you own or have rights to.

## How teammates' Grok Bots report status

Each bot commits or opens a pull request that adds one file:

```
status/<owner>-<bot>.json
```

Example: `status/king-zlatan.json`. Prefix the id with the owner. After it lands:

```bash
python3 dashboard/botstatus.py refresh
```

That rebuilds `status/index.json`, which the control room polls every 10 seconds. Do not hand-edit `status/index.json`.

## License

MIT. Copyright 2026 King Simmons. See [LICENSE](LICENSE).
