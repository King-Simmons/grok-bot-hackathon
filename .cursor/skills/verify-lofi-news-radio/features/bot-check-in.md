# Bot check-in

The bot control room lists agents that have checked in. A check-in is `POST /api/agents`. The panel polls `GET /api/agents` every 3 seconds. Files land in `LOFI_STATUS_DIR`, which this verification run sets outside the repo.

## Sub-features

- `bots-list` returns the live list, which may be empty.
- `bots-check-in` creates one agent and shows it on the following GET.
- `bots-reject` rejects a new id that has no owner, state, or task.

## How to get to it (user POV)

- Open `/dashboard/` and read the card titled Bot control room (`#botList`, `#botSum`).
- A bot posts the same JSON the curl below sends. The listener sees the name, owner, and task in that card.

## Driving it with session.sh

Preconditions:

- Doctor has succeeded for this `$EVIDENCE`.
- `LOFI_STATUS_DIR` in `session.env` is under `$EVIDENCE`, not the repo `status/` directory.
- The id `verify-lofi-bot` is not already in the list. The isolated status dir starts empty, so this holds for a fresh launch.

- **Empty room.** Run `curl -s -D - -o "$EVIDENCE/proof/bots-before.body.json" "$BASE/api/agents"`. Status `200`. `count` matches the length of `bots`. On a fresh evidence dir, `count` is `0`.
- **Check in.** Run `curl -s -D - -o "$EVIDENCE/proof/bots-post.body.json" -X POST "$BASE/api/agents" -H 'Content-Type: application/json' -d '{"id":"verify-lofi-bot","name":"Verify Bot","owner":"Verify","state":"running","task":"Proving the control room"}'`. Status `200`. The body `id` is `verify-lofi-bot`, `state` is `running`, and `presence` is `online`.
- **Read it back.** Run `curl -s -D - -o "$EVIDENCE/proof/bots-after.body.json" "$BASE/api/agents"`. Status `200`. `bots` contains one object whose `id` is `verify-lofi-bot` and whose `task` is `Proving the control room`. The post body alone is not the proof.
- **Reject a partial create.** Run `curl -s -D - -o "$EVIDENCE/proof/bots-partial.body.json" -X POST "$BASE/api/agents" -H 'Content-Type: application/json' -d '{"id":"verify-lofi-partial"}'`. Status `400`. The body has an `error` string. A follow-up GET does not list `verify-lofi-partial`.

## Gotchas

- Ids use lowercase letters, digits, `.`, `_`, and `-`. `index` is reserved. An uppercase id fails before a file is written.
- `running` and `idle` with no update for 30 minutes show as `offline`. The file stays. A verification run is inside that window, so a fresh check-in is `online`.
- Cleanup deletes `$EVIDENCE/status`. Do not `clear --all` against the repo `status/` directory.
- More than 48 live agents returns `409`. An update of an existing id still works.
