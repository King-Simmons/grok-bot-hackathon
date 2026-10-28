# Learn a topic

Learn a topic takes a short phrase, reads a few public pages when the source is Web, and returns a lofi track. The form is the default tab in the control room. Web is selected unless the listener picks X or opens `?source=x`.

## Sub-features

- `topic-catalog` lists cached topics from `GET /api/topics` without recording a usage event.
- `topic-empty` rejects a blank topic.
- `topic-bad-source` rejects a source other than `web` or `x`.
- `topic-web` renders a short web topic and returns `track_url`.

## How to get to it (user POV)

- Open `/dashboard/`. The selected tab is `Learn a topic → lofi` (`#tabX`). Type into `#xTopic` and submit LEARN IT LOFI (`#xBtn`).
- Choose Web (`#srcWeb`) or X (`#srcX`). Web is the default.
- Open `/?topic=lofi` or `/?topic=lofi&source=web`. `?source=x` selects X.

## Driving it with session.sh

Preconditions:

- Doctor has succeeded for this `$EVIDENCE`.
- This run is the only one synthesizing.
- Do not set `X_BEARER_TOKEN` for this journey. X without a token uses the committed `x_topics.json` cache. A missing token is the normal public-repo state.

- **Catalog.** Run `curl -s -D - -o "$EVIDENCE/proof/topics.body.json" "$BASE/api/topics"`. Status `200`. The JSON has `topics`, `"default_source": "web"`, and `"sources"` containing `web` and `x`. `live_search` is false when `X_BEARER_TOKEN` is unset.
- **Empty topic.** Run `curl -s -D - -o "$EVIDENCE/proof/topic-empty.body.json" -X POST "$BASE/api/synthesize_topic" -H 'Content-Type: application/json' -d '{"topic":"","agent_id":"verify-lofi"}'`. Status `400`. The body has `"error_kind": "empty_topic"`.
- **Bad source.** Run `curl -s -D - -o "$EVIDENCE/proof/topic-bad-source.body.json" -X POST "$BASE/api/synthesize_topic" -H 'Content-Type: application/json' -d '{"topic":"lofi","source":"radio","agent_id":"verify-lofi"}'`. Status `400`. The body has `"error_kind": "bad_source"`.
- **Web topic.** Run `curl -s -D - -o "$EVIDENCE/proof/topic-lofi.body.json" -X POST "$BASE/api/synthesize_topic" -H 'Content-Type: application/json' -d '{"topic":"lofi","source":"web","agent_id":"verify-lofi"}'`. Status `200`. The JSON has `track_url` and `topic`. Follow `track_url` with a second GET, the same way as paste-a-link, and require status `200` and a body larger than 1000 bytes.

## Gotchas

- An unknown topic is `404` with `"error_kind": "topic_not_found"`. That is a product response, not a transport failure.
- Web search reads Wikipedia, a public news RSS feed, and Hacker News. A refused fetch falls back to the words in the URL. If egress is blocked, record that precondition instead of switching the topic to a cached X phrase to make the call pass.
- `posts` is used only when `source` is `x`. Sending posts with `source` `web` does not change the web search.
- The suggestion chips under the form come from `GET /api/topics`. An empty `topics` array means `x_topics.json` had nothing to show. The web render can still succeed.
