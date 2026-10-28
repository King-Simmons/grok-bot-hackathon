# Control room

Opening the control room shows the Lofi News Radio clock, the player, the paste-a-link card, the usage meter, the bot list, and the headline ticker. The page is served by the same process as the API.

## Sub-features

- `room-health` answers `GET /api/health` with `"ok": true` before any other drive.
- `room-shell` serves `/dashboard/` with the title, radio tab, news-graph tab, both forms, the player, and the bot list.
- `room-polls` returns agents, usage, topics, headlines, and the episode sidecar the page reads.
- `room-beat` serves the committed demo beat at `/samples/beat-001.mp3`.
- `room-dom` renders station presets and the graph view when a headless Chrome is available.

## How to get to it (user POV)

- Run `./run.sh` and open `/dashboard/` in a browser.
- `GET /` redirects to `/dashboard/`.

## Driving it with session.sh

Preconditions:

- `scripts/session.sh doctor` has succeeded for this `$EVIDENCE`.
- `$BASE` comes from `$EVIDENCE/session.env`.

- **Open the room.** Capture the page and the polls it makes. Run `.cursor/skills/verify-lofi-news-radio/scripts/session.sh capture-control-room "$EVIDENCE"`. Exit code `0`. `$EVIDENCE/proof/health.json` has `"ok": true`. `$EVIDENCE/proof/dashboard.html` contains `Lofi News Radio · Dashboard`, `id="tabRadio"`, `id="tabGraph"`, `id="u2lUrl"`, `id="xTopic"`, `id="botList"`, `id="playBtn"`, and `id="rDial"`. `agents.json` has `bots` and `protocol`. `usage.json` has `today` and `all_time`. `topics.json` has `"default_source": "web"`. `headlines.json` reports source `Hacker News` and a count greater than zero. `episode.json` has `"podcast_audio": "host_briefing.mp3"`. `beat-001.size.txt` is greater than 1000.
- **Redirect.** Request the origin. Run `curl -s -o /dev/null -D - "$BASE/"`. Status `302` and `Location: /dashboard/`.
- **Browser render.** When Chrome is on `PATH`, the same capture writes `dashboard.dom.html`, `dashboard.png`, and `graph.dom.html`. The radio DOM contains `data-st="lofi"`, `data-st="talk"`, and `id="rFreq"`. The graph DOM has `id="viewGraph"` without `hidden` on that tag. If `proof/browser-skipped.txt` exists, the DOM step was not run. Say so. Do not mark `room-dom` verified.

## Gotchas

- `./run.sh` prints `Open http://...` before the process is listening. Wait for doctor, not for that line.
- The bot list stays on "Looking for agents…" until `GET /api/agents` returns. An empty `bots` array is a valid quiet room.
- Headless Chrome in this environment needs `--no-sandbox`. `capture-control-room` already passes it. A missing browser is a skipped DOM proof, not a failed HTTP proof.
- `dashboard/tracks.json` is rewritten by `./run.sh` and is gitignored. A dirty git status on that file is expected during a run and should be absent after the file is left untracked.
