# Lofi News Radio verification map

This directory is the maintained source for verifying the listener-facing behavior of Lofi News Radio. Read the index before driving the app, then use the matching feature file as the recipe.

## Baseline preconditions

- Launch with `scripts/session.sh launch` so the instance has its own port, `LOFI_STATUS_DIR`, and `LOFI_DATA_DIR`.
- Run `scripts/session.sh doctor` and require `{"ok": true}` from `/api/health` on that port.
- Source `$EVIDENCE/session.env` before any curl. `$BASE` is the origin.
- The committed `dashboard/headlines.json`, `dashboard/x_news.json`, `dashboard/episode.json`, and `samples/beat-001.mp3` are the page's seed data.
- Never drive an instance this verification run did not start. Leave port 53343 alone unless this run's `session.env` says that port.

## Driving conventions

- Start every recipe from the baseline state unless its preconditions say otherwise.
- Prefer element ids and accessible names from `dashboard/index.html` over coordinates.
- Treat every command as literal. Keep quoted JSON and flags unchanged.
- HTTP actions use `curl` against `$BASE`.
- The control-room page capture uses `scripts/session.sh capture-control-room`.
- Restore nothing in the repo. Scratch status and usage live under `$EVIDENCE` and cleanup deletes them. Do not remove proof artifacts during cleanup.

## Proof and skip reporting

- Capture the request and the resulting body, not only a status code.
- Dashboard proof includes the saved HTML and, when Chrome is on `PATH`, `dashboard.dom.html` plus `dashboard.png`.
- API proof includes the curl command, the status, and the JSON body under `$EVIDENCE/proof/`.
- Mutation proof includes a follow-up GET that shows the new value.
- Record the feature id and the entry point with every artifact.
- Report an unreachable path with the attempted command and the unmet precondition.
- Do not report a skipped entry point as verified through a different path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the listener-visible behavior. It then uses exactly four H2 sections in this order.

1. `Sub-features` lists short IDs with one line for each behavior.
2. `How to get to it (user POV)` lists every user entry point.
3. `Driving it with session.sh` starts with `Preconditions:` and uses labeled bullets that pair each user action with an exact command and observable result.
4. `Gotchas` lists traps that can waste or invalidate a verification run.

## Features

- [Control room](./control-room.md) covers the dashboard shell, health, usage, headlines, and the demo beat.
- [Paste a link](./paste-a-link.md) covers the URL form and `POST /api/synthesize`.
- [Learn a topic](./learn-a-topic.md) covers the topic form, `GET /api/topics`, and `POST /api/synthesize_topic`.
- [Bot check-in](./bot-check-in.md) covers the bot panel and `POST /api/agents`.
- [Radio and News Graph](./radio-and-news-graph.md) covers 88.1 LOFI, 101.5 NEWS TALK, and the `#graph` view.
