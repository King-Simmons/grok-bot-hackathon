# Lofi News Radio / URL-to-Lofi Synthesizer

A Grok Bot hackathon entry for **Track 2: Build for the Bot Economy**.

Lofi News Radio is a control room for a small crew of bots, plus a URL-to-lofi engine. Paste a public link. The engine reads the page, scores a mood deterministically (no LLM), and renders an original lofi track. Other bots can call the same pipeline through MCP.

![Lofi News Radio dashboard](docs/dashboard.png)

*Control room, Oct 19, 2026, 6:56 PM CT.*

Built by King Simmons with his Grok Bot, Zlatan. PRD by Jonathan Chen, *URL-to-Lofi Synthesizer*. The team PRD and research notes live in the team's Google Drive folder.

## Repo layout

| path | what |
|---|---|
| `dashboard/index.html` | Control room: clock, CSS lofi scene, player, Web Audio mixer sliders, bot panel, paste-a-link box |
| `dashboard/botstatus.py` | Stdlib status CLI. Writes repo-root `status/` and `dashboard/tracks.json` |
| `dashboard/status` | Symlink to `../status`, so the page's relative `status/index.json` fetch works |
| `dashboard/*.json` | Sample headlines, inbox, and episode data |
| `engine/` | URL fetch, mood JSON, code-synthesized beat, local HTTP API |
| `mcp/` | MCP server: `synthesize_url`, `get_mood_analysis` |
| `audio/` | Original beat generator, plot, vocal lofi chain, and Chatterbox voice (`voice.py`) |
| `samples/beat-001.mp3` | One original code-synthesized demo beat |
| `docs/` | Status protocol, [voice notes](docs/VOICE.md), and the dashboard screenshot |
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
POST /api/speak {"text": "...", "preset": "news"}
```

`force: true` re-renders even when that track already exists. Async jobs are polled at `GET /api/jobs/<id>`.

`/api/speak` is a thin wrapper around `audio.voice.synthesize_speech(text, exaggeration, cfg_weight, speed, out_path)`. It returns `503` when chatterbox-tts is not installed. The music routes still work. See [docs/VOICE.md](docs/VOICE.md).

### MCP

See [mcp/README.md](mcp/README.md). From the repo root, after `./run.sh` has created the venv:

```bash
.venv/bin/python mcp/test_mcp_client.py https://example.com
```

## Radio stations (Lofi / News Talk)

The Now playing card has a retro FM tuner. Same player, same beat; the station decides what rides on top.

- **88.1 LOFI**: the beat, as before. Turn on **Drops** for a one-line headline read about every 30 seconds, with the beat ducked under it.
- **101.5 NEWS TALK**: a host reads a short briefing over the beat (intro, each headline from `headlines.json` with a one-line take, outro) and repeats it every 20 seconds after it ends. The music ducks while the host talks and comes back between briefings. The headline being read is shown under the tuner and highlighted in the ticker, and the ON AIR light glows.

Tune by clicking a preset, dragging the needle, scrolling, or with the keyboard on the dial (arrows ±0.1 MHz, Shift+arrows ±1, Page Up/Down or Home/End jump stations). Between stations you hear static synthesized with Web Audio (no samples), which crossfades into the station as the needle locks on. The last station is saved in `localStorage` (`lofiDash.radio.v1`). Press ▶ to go on air after a reload, because browsers block audio and speech until you click.

Host voice, in order of preference:

1. **Pre-rendered audio.** If `dashboard/episode.json` has `"podcast_audio": "../tracks/briefing-001.mp3"` (for example a Chatterbox render), News Talk plays that file through the Voice slider instead. Optional `"podcast_cues": [{"t": 0, "headline": -1}, {"t": 6.5, "headline": 0}]` drives the headline highlight.
2. **Browser speech** (`speechSynthesis`). Zero dependencies. It picks a good English voice when one is installed (Google US English, Samantha, any "Natural" or "Neural" voice). The Voice slider sets the host volume for each sentence; the Music slider still sets the beat.
3. **Captions.** If the browser has the API but no voice, for example headless Chrome, the briefing runs as timed captions. If `speechSynthesis` is missing entirely, the tuner stays on 88.1 and says why.

The host only says what is in `headlines.json`: the title, the source, and, if a headline has them, optional `take` or `summary` fields. Today those headlines are placeholders, and the intro says so.

Pre-render a host line with Chatterbox instead of speaking live. On CPU the model takes about 8 seconds to load and about 2 seconds of compute per second of audio. The function other modes should call is `synthesize_speech(text, exaggeration, cfg_weight, speed, out_path)` in `audio/voice.py`. Install, the watermark, and the no-cloning rule are in [docs/VOICE.md](docs/VOICE.md).

```bash
python3 -m audio.voice --text "Good evening. You're listening to News Talk." --preset news --out tracks/host-line.wav
```

## Dependencies

`./run.sh` installs [requirements.txt](requirements.txt): numpy, scipy, certifi, and optional pyloudnorm, lameenc, trafilatura, and vaderSentiment. `mcp` is required for the MCP server.

`ffmpeg` on `PATH` is the fallback MP3 encoder and is used by `audio/lofi_vocals.py` and by the voice `speed` control (`atempo`). `demucs` is optional and is not installed by `requirements.txt`. Only pass `--separate` when you have installed it yourself, and only on audio you own or have rights to.

The voice engine is optional and is not installed by `./run.sh`. It needs Python 3.11, [requirements-voice.txt](requirements-voice.txt), and `setuptools<81` (newer setuptools crashes the watermark library while Chatterbox loads). See [docs/VOICE.md](docs/VOICE.md).

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
