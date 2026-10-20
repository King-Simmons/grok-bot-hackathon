# Lofi News Radio / URL-to-Lofi Synthesizer

A Grok Bot hackathon entry for **Track 2: Build for the Bot Economy**.

Lofi News Radio is a control room for a small crew of bots that turn the day's news into a calm, original lofi episode. The page you can run today is the control room: a clock, a CSS rainy-night scene, a player, mixer knobs that synthesize rain and vinyl in the browser, and a bot panel. Next to it, still in progress, is a URL-to-lofi engine: fetch a page, score its mood, and synthesize an original beat.

![Lofi News Radio dashboard](docs/dashboard.png)

*Control room, Oct 19, 2026, 6:56 PM CT.*

The team PRD and research notes live in the team's Google Drive folder.

Built by King Simmons with his Grok Bot, Zlatan. PRD by Jonathan Chen.

## Repo layout

```
dashboard/     Control room (index.html) plus example headlines, inbox, and episode data
               botstatus.py writes status and the track list
dashboard/status -> ../status    symlink so the page can read repo-root status files
engine/        WORK IN PROGRESS URL-to-lofi pipeline (do not treat this as finished)
audio/         Original beat generator, waveform plot, and vocal lofi chain
samples/       beat-001.mp3, one original code-synthesized demo (~1 MB)
docs/          Status protocol and the dashboard screenshot
status/        One JSON file per bot. example-zlatan.json is the sample check-in
```

The control room loads data with relative URLs (`headlines.json`, `episode.json`, `inbox.json`, `tracks.json`, `status/index.json`). Those files sit next to `dashboard/index.html`, except bot status, which teammates commit at the repo root under `status/`. `dashboard/status` is a symlink to that directory, so the page works when you serve the **repository root**. The page itself is unchanged.

`dashboard/episode.json` points at `../samples/beat-001.mp3`, the demo beat.

## Quickstart

Python 3.9+ (for `zoneinfo`). From the repository root:

```bash
python3 dashboard/botstatus.py refresh
python3 -m http.server 8000
```

Open <http://127.0.0.1:8000/dashboard/>. Do not open the file directly (`file://`); the page fetches JSON.

`refresh` rebuilds `status/index.json` (gitignored) from every `status/*.json` and writes `dashboard/tracks.json` so the player can list `samples/beat-001.mp3`. Headlines, inbox, and the episode file load even before that.

### Bot status

```bash
python3 dashboard/botstatus.py set \
  --id king-zlatan --name Zlatan --owner King --role "main assistant" \
  --state running --task "Coordinating hackathon build"
python3 dashboard/botstatus.py list
```

Stdlib only. See [docs/STATUS_PROTOCOL.md](docs/STATUS_PROTOCOL.md).

### Engine (work in progress)

`engine/` is the unfinished URL-to-lofi pipeline: ingest a URL, deterministic mood analysis that emits a JSON prompt schema, a parameterized code-synthesized beat, and a local HTTP API. It is committed as-is. It is not done.

From the repo root. The first run creates `./.venv`, installs `requirements.txt`, and points `SSL_CERT_FILE` at certifi's CA bundle so HTTPS works on python.org macOS Python:

```bash
./run.sh
./run.sh --port 53343
./run.sh --mcp
```

`./run.sh` serves this repo and the API on `http://127.0.0.1:53343/`. `./run.sh --mcp` also starts `mcp/lofi_mcp_server.py` when that file is in the checkout.

Endpoints:

- `GET /api/health`
- `GET /api/mood?url=https://example.com`
- `POST /api/synthesize` with `{"url": "https://example.com"}` (optional `"async": true`, `"force": true`)

Known gap, left unfinished on purpose: `engine/pipeline.py` still shells out to `botstatus.py` at the repo root. The CLI in this layout is `dashboard/botstatus.py`. Mood and synth code do not depend on that call; status updates from the engine will no-op until that path is wired.

## Radio stations (Lofi / News Talk)

The Now playing card has a retro FM tuner. Same player, same beat; the station decides what rides on top.

- **88.1 LOFI**: the beat, as before. Turn on **Drops** for a one-line headline read about every 30 seconds, with the beat ducked under it.
- **101.5 NEWS TALK**: a host reads a short briefing over the beat (intro, each headline from `headlines.json` with a one-line take, outro) and repeats it every 20 seconds after it ends. The music ducks while the host talks and comes back between briefings. The headline being read is shown under the tuner and highlighted in the ticker, and the ON AIR light glows.

Tune by clicking a preset, dragging the needle, scrolling, or with the keyboard on the dial (arrows ±0.1 MHz, Shift+arrows ±1, Page Up/Down or Home/End jump stations). Between stations you hear static synthesized with Web Audio (no samples), which crossfades into the station as the needle locks on. The last station is saved in `localStorage` (`lofiDash.radio.v1`). Press ▶ to go on air after a reload, because browsers block audio and speech until you click.

Host voice, in order of preference:

1. **Pre-rendered audio.** If `dashboard/episode.json` has `"podcast_audio": "../tracks/briefing-001.mp3"` (for example a Chatterbox render), News Talk plays that file through the Voice knob instead. Optional `"podcast_cues": [{"t": 0, "headline": -1}, {"t": 6.5, "headline": 0}]` drives the headline highlight.
2. **Browser speech** (`speechSynthesis`). Zero dependencies. It picks a good English voice when one is installed (Google US English, Samantha, any "Natural" or "Neural" voice). The Voice knob sets the host volume for each sentence; the Music knob still sets the beat.
3. **Captions.** If the browser has the API but no voice, for example headless Chrome, the briefing runs as timed captions. If `speechSynthesis` is missing entirely, the tuner stays on 88.1 and says why.

The host only says what is in `headlines.json`: the title, the source, and, if a headline has them, optional `take` or `summary` fields. Today those headlines are placeholders, and the intro says so.

## Dependencies

The dashboard and `botstatus.py` need only Python 3.9+.

Audio and the engine import the packages in [requirements.txt](requirements.txt):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`certifi` supplies the CA bundle `engine/ingest.py` uses for HTTPS. `ffmpeg` on `PATH` encodes MP3s and runs `audio/lofi_vocals.py`. `demucs` is optional and only used when you pass `--separate`. `trafilatura`, `vaderSentiment`, and `lameenc` are optional; the engine falls back to the stdlib HTML parser, a keyword lexicon, and ffmpeg.

## How teammates' Grok Bots report status

Personal bots do not write to this checkout directly. Each bot commits or opens a pull request that adds **one** file:

```
status/<owner>-<bot>.json
```

Example: `status/king-zlatan.json`. The id inside the file matches the filename without `.json`, and the id is prefixed with the owner so two bots do not collide. Field rules, states, and the stale-after-30-minutes rule are in [docs/STATUS_PROTOCOL.md](docs/STATUS_PROTOCOL.md).

After the file lands, anyone with the repo can run:

```bash
python3 dashboard/botstatus.py refresh
```

That rebuilds `status/index.json`, which the control room polls every 10 seconds. Do not hand-edit `status/index.json`.

## License

MIT. Copyright 2026 King Simmons. See [LICENSE](LICENSE).
