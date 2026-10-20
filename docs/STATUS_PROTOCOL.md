# Lofi News Radio: Bot Status Protocol (v1)

One small JSON file per bot or job tells the dashboard what it is doing. That gives
King a single control room for every bot, including teammates' Grok Bots.

## Where
- `status/<id>.json` (repository root): one file per bot/job. `<id>` uses lowercase letters, digits, `.`, `_`, `-` (max 64). `index` is reserved.
- `status/index.json`: generated roll-up the dashboard reads every 10 s. Never hand-edit it; it is rebuilt by `dashboard/botstatus.py` on every `set`, `clear`, `list` and `refresh`.
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
2. While `running`, update at least every 30 minutes. A `running` or `idle` bot with no update for 30 min shows as **stale** on the dashboard. `done` and `error` never go stale.
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

## Teammates' bots (later)
Personal Grok Bots can't write to this box directly. Planned options:
- **Shared GitHub repo:** each bot commits `status/<owner>-<bot>.json`; a sync step pulls the folder here and runs `refresh`.
- **Shared Google Drive folder:** same files in a `status/` folder, pulled down periodically.
Prefix ids with the owner (e.g. `alex-news-gatherer`) to avoid collisions.
