# Learn a topic → lofi

The dashboard topic card (the same card as paste-a-link) takes a short topic and renders an original lofi track from public text. A source selector sits above the topic box.

- **Web** is the default. It does not need an API key.
- **X** is optional. It is the post path already on this branch: live X API v2 when `X_BEARER_TOKEN` is set, otherwise the cached `x_topics.json` snapshot.

`?topic=lofi` runs a search. `?source=web` or `?source=x` picks the source and wins over the value saved in `localStorage` (`lofiDash.topicSource`).

## Web

Discovery uses an identifying user agent (`LofiNewsRadio-TopicLofi/1.0`) and does not retry with a browser user agent when a host says no.

1. Wikipedia search (`opensearch`, then the REST page search).
2. Wikinews search API.
3. Public Bing News RSS. The article URL already inside the feed is what gets fetched. Google News RSS is not used.
4. Hacker News via the Algolia story search. Story URLs are preferred over discussion pages.

Each chosen URL goes through `engine.ingest`. A refused fetch uses the existing url-words fallback (the words in the link). Those pages are kept when fewer than two pages returned real article text. The mood is built from the combined text. The response lists the pages as links and does not include their full text.

Web code lives in `engine/topic_web.py`. It calls `pipeline.synthesize_text` and does not add a second HTTP route.

## API

`GET /api/topics` still returns the X suggestion chips, plus `default_source: "web"` and `sources: ["web", "x"]`.

```
POST /api/synthesize_topic
{"topic": "lofi", "source": "web", "async": true, "agent_id": "dashboard"}
```

| Case | Status | `error_kind` |
| --- | --- | --- |
| Empty topic, or a topic with no letters or digits | 400 | `empty_topic` |
| `source` other than `web` or `x` | 400 | `bad_source` |
| No public pages, or no X posts in the cache and no token | 404 | `topic_not_found` |

`source` defaults to `web`. `posts` is ignored unless `source` is `x`. On X, passing posts with a blank topic is still treated as the topic `custom posts`.

A web result sets `topic.source` to `"web"`, `topic.requested_source` to `"web"`, and `topic.origin` to `"web-search"`. `topic.pages` is `{title, url, via, method, words}` and, when the fetch was refused, `note`. An X result keeps `topic.source` as the post origin (`x-api-live`, `x_topics.json`, or `provided`) and sets `topic.requested_source` to `"x"`.

## MCP

`synthesize_topic(topic, posts=None, force=False, source="web", agent_id="")` on `mcp/lofi_mcp_server.py`.

Web returns `pages`, `page_count`, `source`, and `note` along with the mood JSON and track path. X still returns `what_people_are_saying`, `post_count`, `post_source`, and `x_search_url`. Empty topic, bad source, unknown topic, and ingest failures are tool errors with the same sentences as the HTTP API.

## Check

```bash
.venv/bin/python scripts/smoke_topic.py
```

That exercises web search (network), the X cache with the token unset, the empty-topic and bad-source errors, and the HTTP helpers on `route_synthesize_topic_post`.
