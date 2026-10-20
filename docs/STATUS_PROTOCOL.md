# Lofi News Radio: Bot Status Protocol (v1)

One small JSON file per bot or job tells the dashboard what it is doing. That gives
King a single control room for every bot, including teammates' Grok Bots.

## Where
- `status/<id>.json` (repository root): one file per bot/job. `<id>` uses lowercase letters, digits, `.`, `_`, `-` (max 64). `index` is reserved.
- `status/index.json`: generated roll-up. Never hand-edit it; it is rebuilt by `dashboard/botstatus.py` on every `set`, `clear`, `list` and `refresh`, and whenever an agent checks in over HTTP. The control room prefers `GET /api/agents` (every 3 s) and falls back to this file.
- `dashboard/tracks.json`: generated list of playable mp3s for the Now playing picker (same rebuilds). The CLI picks up `samples/beat-*.mp3` and `tracks/*.mp3`. Optional sidecar `beat-NNN.json` with `title`, `bpm`, `key`, `duration_s` or `notes` adds metadata.

The dashboard fetches `status/index.json` relative to `dashboard/index.html`. `dashboard/status` is a symlink to the repo-root `status/` directory, so serving the repo root is enough. Do not rewrite those fetches.

## Fields
| field | required | meaning |
|---|---|---|
| `id` | yes | stable id, same as the file name |
| `name` | yes | display name (defaults to id) |
| `owner` | yes | the person the bot works for, e.g. `King` |
| `role` | no | short description, e.g. `main assistant` |
| `state` | yes | `running`, `idle`, `done` or `error` |
| `task` | yes | one line: what it is doing now / did last |
| `last_update` | yes | ISO 8601 with offset, America/Chicago, e.g. `2026-10-19T18:28:42-05:00` |
| `progress` | no | integer 0-100 |
| `artifact` | no | relative path in the repo (e.g. `samples/beat-001.mp3`) or an http(s) URL |
| `error` | no | error message (auto-removed when state leaves `error`, unless `--keep-error`) |

Example `status/beat-maker.json`:
```json
{
  "id": "beat-maker",
  "name": "Beat Maker",
  "owner": "King",
  "role": "beat + vocal render",
  "state": "running",
  "task": "Rendering an original instrumental for the morning news",
  "last_update": "2026-10-19T18:28:42-05:00",
  "progress": 40,
  "artifact": "samples/beat-001.mp3"
}
```

## Rules for bots
1. Check in when you start (`running`), on meaningful progress, and when you finish (`done`) or fail (`error` + `--error`).
2. While `running` or `idle`, check in at least every 60 seconds. No update for **120 seconds** and the dashboard shows that agent as **offline** (the file is kept; a later check-in brings it back). `done` and `error` never go offline.
3. Write atomically (temp file + rename). `botstatus.py` does this for you.
4. Only write your own `<id>.json`. Remove it with `clear` when the bot or job is retired.

## CLI (stdlib Python 3.9+, no installs)

From the repository root (`dashboard/botstatus.py`). The same commands work as `python3 botstatus.py ...` if your shell is already in `dashboard/`.

```
python3 dashboard/botstatus.py set --id beat-maker --name 'Beat Maker' --owner King --state running --task 'Rendering an original instrumental' [--role '...'] [--progress 40] [--artifact samples/beat-001.mp3]
python3 dashboard/botstatus.py set --id beat-maker --progress 80                     # partial update; other fields kept
python3 dashboard/botstatus.py set --id beat-maker --state done --task 'instrumental rendered' --progress 100 --artifact samples/beat-001.mp3
python3 dashboard/botstatus.py set --id beat-maker --state error --error 'ffmpeg missing'
python3 dashboard/botstatus.py set --id beat-maker --no-progress --no-artifact      # drop optional fields
python3 dashboard/botstatus.py clear --id beat-maker      # or: clear --all
python3 dashboard/botstatus.py list                        # table; list --json for raw
python3 dashboard/botstatus.py refresh                     # rebuild index.json + tracks.json only
```
`set` merges with the existing file, so after the first call only changed fields are needed.
If you write a status file by hand (or sync one in), run `python3 dashboard/botstatus.py refresh` so it appears in `index.json`.

## Check in over HTTP

The engine server (`./run.sh`, port 53343, localhost only) accepts the same fields. The first post creates the agent. Later posts merge, and only the fields you send change. `id` is required every time. A brand-new id also needs `owner`, `state`, and `task` (`name` defaults to the id).

```bash
curl -s -X POST http://127.0.0.1:53343/api/agents -H 'Content-Type: application/json' -d '{"id":"alex-news","name":"News Gatherer","owner":"Alex","state":"running","task":"Pulling headlines"}'
```

`GET /api/agents` returns the same records plus `presence` (`online`, `offline`, or `settled`) and `stale_after_s` (120). Bodies must be JSON, at most 8 KB. Ids use the same pattern as the files. More than 48 agents is rejected (updates of an existing id still work). There is no auth token in the repo; the server binds to 127.0.0.1 unless you pass `--host`.

The old placeholder `status/example-zlatan.json` is not part of the live list. `docs/examples/bots.json` is a retired sample roster and is not shown.

## Check in over MCP

From a bot that has this repo's MCP server (`mcp/lofi_mcp_server.py`):

```text
report_status(id="alex-news", name="News Gatherer", owner="Alex", state="running", task="Pulling headlines")
```

`list_agents()` returns the same payload as `GET /api/agents`. Pass `agent_id` when you want the usage log to credit a caller other than the status id.

## Usage meter

`GET /api/usage` and the MCP tool `get_usage()` read a local log under `data/` (gitignored). They report calls, errors, tracks, audio minutes, and X reads for today and all-time, a per-agent table, and counts for the last 24 hours. The raw log rotates so it stays small; the totals file does not shrink when it rotates. Nothing is sent off the machine. X reads move only when a live search records them (`x_search`), and the panel compares that count with a local free-tier budget (`LOFI_X_READ_CAP`, default 100 per month).

## Teammates' bots
Any agent that can reach the engine server can check in with the curl above or `report_status`. Writing `status/<id>.json` still works: run `python3 dashboard/botstatus.py refresh` so `status/index.json` matches. Prefix ids with the owner (e.g. `alex-news-gatherer`) to avoid collisions.
