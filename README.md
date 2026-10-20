# Lofi News Radio / URL-to-Lofi Synthesizer

A Grok Bot hackathon entry for **Track 2: Build for the Bot Economy**.

Lofi News Radio is a control room for a small crew of bots that turn the day's news into a calm, original lofi episode. The page you can run today is the control room: a clock, a CSS rainy-night scene, a player, mixer knobs that synthesize rain and vinyl in the browser, and a bot panel. Next to it, still in progress, is a URL-to-lofi engine: fetch a page, score its mood, and synthesize an original beat.

![Lofi News Radio dashboard](docs/dashboard.png)

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

From the repo root, after the pip installs below:

```bash
python3 engine/server.py --port 53343
```

That process serves this repo and the API on `http://127.0.0.1:53343/`:

- `GET /api/health`
- `GET /api/mood?url=https://example.com`
- `POST /api/synthesize` with `{"url": "https://example.com"}` (optional `"async": true`, `"force": true`)

Known gap, left unfinished on purpose: `engine/pipeline.py` still shells out to `botstatus.py` at the repo root. The CLI in this layout is `dashboard/botstatus.py`. Mood and synth code do not depend on that call; status updates from the engine will no-op until that path is wired.

## Dependencies

The dashboard and `botstatus.py` need only Python 3.9+.

Audio and the engine import the packages in [requirements.txt](requirements.txt):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`ffmpeg` on `PATH` is used to encode MP3s and to run `audio/lofi_vocals.py`. `demucs` is optional and only used when you pass `--separate`. `trafilatura`, `vaderSentiment`, and `lameenc` are optional; the engine falls back to the stdlib HTML parser, a keyword lexicon, and ffmpeg.

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
