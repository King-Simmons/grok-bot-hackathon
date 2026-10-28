# Paste a link

Paste a link takes a public page URL, scores a mood, and returns an original track the player can open. The form is the "Paste a link" tab in the control room. The request is `POST /api/synthesize`.

## Sub-features

- `link-open` shows the URL field and the MAKE IT LOFI button.
- `link-reject-empty` rejects a missing URL.
- `link-render` accepts `https://example.com` and returns a `track_url` that serves audio.
- `link-async` returns a job id when `"async": true`, and `GET /api/jobs/<id>` reaches `done`.

## How to get to it (user POV)

- Open `/dashboard/`, choose the `Paste a link` tab (`#tabU`), type a URL into `#u2lUrl`, and submit MAKE IT LOFI (`#u2lBtn`).
- `/?link=https://example.com` pre-fills the same field. The page still waits for the submit button.

## Driving it with session.sh

Preconditions:

- Doctor has succeeded for this `$EVIDENCE`.
- This run is the only one synthesizing. `tracks/` is shared by every instance.
- Network egress can reach `https://example.com`. If it cannot, stop this journey and record the unmet precondition. Do not point the request at a different host to force a pass.

- **Form is present.** The saved dashboard HTML from the control-room capture contains `id="tabU"`, `id="u2lUrl"`, and `MAKE IT LOFI`.
- **Reject a missing URL.** Run `curl -s -D - -o "$EVIDENCE/proof/paste-empty.body.json" -X POST "$BASE/api/synthesize" -H 'Content-Type: application/json' -d '{"url":""}'`. Status `400`. The body has `"error": "missing url"`.
- **Render example.com.** Run `curl -s -D - -o "$EVIDENCE/proof/paste-example.body.json" -X POST "$BASE/api/synthesize" -H 'Content-Type: application/json' -d '{"url":"https://example.com","agent_id":"verify-lofi"}'`. Status `200`. The JSON has `mood`, `track_url`, and `track`. `track_url` starts with `/tracks/`.
- **Play the file.** Run `curl -s -o /dev/null -D "$EVIDENCE/proof/paste-example.track.headers" -w '%{http_code} %{size_download}\n' "$BASE$(python3 -c 'import json,os; print(json.load(open(os.environ["EVIDENCE"]+"/proof/paste-example.body.json"))["track_url"])')"`. Status `200` and the body is larger than 1000 bytes. That second GET is the proof the track exists. The JSON alone is not.
- **Async job.** Run `curl -s -D - -o "$EVIDENCE/proof/paste-async.body.json" -X POST "$BASE/api/synthesize" -H 'Content-Type: application/json' -d '{"url":"https://example.com","async":true,"agent_id":"verify-lofi"}'`. Status `202`. The body has `job_id` and `poll`. Poll `GET $BASE/api/jobs/<job_id>` until `state` is `done` or `error`. A `done` job's `result.track_url` is the same shape as the sync response.

## Gotchas

- A sync render fetches the page and synthesizes audio. Give it a few minutes before calling it hung. Read `$EVIDENCE/server.log` for the traceback.
- `"vocals"` other than `summary` or `song` is treated as `none`. The dashboard does not send vocals. Do not install Chatterbox for this journey.
- `force: true` re-renders an existing track. Leave it off unless the journey is specifically about a rebuild.
- An empty JSON array, a string, or `null` is `400` with a message that the body must be a JSON object. That is a different failure from a missing URL.
