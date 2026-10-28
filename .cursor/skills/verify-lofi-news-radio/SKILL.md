---
name: verify-lofi-news-radio
description: "Drive Lofi News Radio the way a listener does: start ./run.sh on its own port, check /api/health, open the dashboard, hit the key API routes, and run ./scripts/test.sh. Use after a change to the control room, the engine HTTP API, headlines, or the dashboard."
---

# Verify Lofi News Radio

The listener-facing surface is the control room at `/dashboard/`. The same process serves the HTTP API under `/api/`. MCP on port 53350 (`./run.sh --mcp`) is a second surface. This skill drives the control room and the API. It does not call the X API and it does not need `X_BEARER_TOKEN`.

Read `features/README.md` before a pass, then follow one feature file per journey. The harness is `scripts/session.sh` in this skill directory, plus the curl commands in the feature files.

## Tests

From the repository root, run:

```bash
./scripts/test.sh
```

That file is the only test command. A separate change is adding a larger suite and CI. When the runner changes, edit the exec line inside `scripts/test.sh`. Leave this section calling `./scripts/test.sh`.

## Launch

From the repository root, start an isolated instance. The script picks a free port, sets `LOFI_STATUS_DIR` and `LOFI_DATA_DIR` under the evidence directory, and runs `./run.sh`. The first run creates `./.venv` and installs `requirements.txt`. Ready means `GET /api/health` returns `{"ok": true, "jobs": <n>}`, not the earlier "Open http://..." line from `run.sh`.

```bash
EVIDENCE="$(.cursor/skills/verify-lofi-news-radio/scripts/session.sh launch)"
.cursor/skills/verify-lofi-news-radio/scripts/session.sh doctor "$EVIDENCE"
```

`session.env` inside the evidence directory records `BASE`, `PORT`, and `PID`. Source it before a curl in a feature file. Default evidence root is `/tmp/lofi-news-radio-verify/<run-id>/`. Two instances can run together when each has its own port, status dir, and data dir. They still share the repo `tracks/` directory, so do not synthesize the same URL from two instances at once. `./run.sh` also rewrites gitignored `dashboard/tracks.json`.

If the pid exits before doctor succeeds, read `$EVIDENCE/server.log`. On Debian or Ubuntu, `python3 -m venv` fails when `python3-venv` is not installed (`ensurepip is not available`). Install that package, delete the broken `./.venv`, and launch again. A half-created `.venv` still has a `python` binary, and `./run.sh` will keep using it.

Do not drive a server you did not start. The default `http://127.0.0.1:53343` may belong to a person.

## Doctor

```bash
.cursor/skills/verify-lofi-news-radio/scripts/session.sh doctor "$EVIDENCE"
```

Doctor checks that the recorded pid is still `./run.sh` or `engine/server.py`, then waits until `/api/health` returns `"ok": true`. Run it before the first drive and again after any failed drive. If the pid is dead, read `$EVIDENCE/server.log` and launch again.

## Drive

Follow the feature file. HTTP checks use `curl` against `$BASE` from `session.env`. The control-room capture also saves the dashboard HTML, the key JSON routes, a headless Chrome DOM dump, and a screenshot when `google-chrome` or `chromium` is on `PATH`.

```bash
set -a; source "$EVIDENCE/session.env"; set +a
.cursor/skills/verify-lofi-news-radio/scripts/session.sh capture-control-room "$EVIDENCE"
```

`capture-control-room` is the control-room journey. It loads `/dashboard/?hn=0` so the page stays on the committed headline snapshot. The other feature files name their own curl commands. Save each response under `$EVIDENCE/proof/` with the feature id in the filename.

## Evidence

Proof lives in `$EVIDENCE/proof/` and `$EVIDENCE/server.log`. A control-room proof contains `health.json`, `dashboard.html`, `agents.json`, `usage.json`, `topics.json`, `headlines.json`, `episode.json`, and `beat-001.size.txt`. When Chrome is installed it also contains `dashboard.dom.html`, `dashboard.png`, and `graph.dom.html`.

A proof shows the request and the resulting state. A 200 with the wrong body is a failure. For a mutation, read the value back with a second GET. Do not treat a dry name as proof. `./scripts/test.sh` does not fetch the network and does not replace a drive of the running server.

## Cleanup

```bash
.cursor/skills/verify-lofi-news-radio/scripts/session.sh cleanup "$EVIDENCE"
```

Cleanup kills the recorded pid only after checking its command line, then deletes `$EVIDENCE/status` and `$EVIDENCE/data`. It leaves `$EVIDENCE/proof` and `$EVIDENCE/server.log`. Confirm `proof/health.json` still exists after cleanup. Never kill by process name. A failed attempt uses the same cleanup so the port is released.

## Helpers

The script is executable. Invoke it as:

```bash
.cursor/skills/verify-lofi-news-radio/scripts/session.sh launch
.cursor/skills/verify-lofi-news-radio/scripts/session.sh doctor "$EVIDENCE"
.cursor/skills/verify-lofi-news-radio/scripts/session.sh capture-control-room "$EVIDENCE"
.cursor/skills/verify-lofi-news-radio/scripts/session.sh cleanup "$EVIDENCE"
```

The same file is linked from `.claude/skills/verify-lofi-news-radio/` and `.agents/skills/verify-lofi-news-radio/`.

## Maintenance

When the dashboard or the API drifts from `features/`, update the map with the maintain-verification-skill (`vendor/pstack/skills/maintain-verification-skill/SKILL.md`). One correction pass, then re-drive what changed.
