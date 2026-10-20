#!/usr/bin/env python3
"""Pull current X news stories (official X API v2 News endpoint) into dashboard/x_news.json.

Zero dependencies (stdlib only). X items are a separate list from headlines.json, so the
101.5 demo recording (episode.json "podcast_headlines") never stops matching.

    export X_BEARER_TOKEN=...                       # app-only bearer token from developer.x.com
    python3 scripts/fetch_x_news.py                 # "AI technology", last 24 h, tech stories only
    python3 scripts/fetch_x_news.py -q "startups" --max-age 48 -n 20
    python3 scripts/fetch_x_news.py --out -         # print instead of writing

No token? Build the file from saved API responses instead (this is how the committed
snapshot was made, from a bot's X tools: search_news, then get_posts_by_ids):

    python3 scripts/fetch_x_news.py --from-raw news.json [--posts-raw posts.json]

Endpoints: GET https://api.x.com/2/news/search  and  GET https://api.x.com/2/tweets
Only fields the API returns are written; nothing is filled in or estimated.
"""
import argparse, json, os, re, sys, tempfile, urllib.parse, urllib.request
from datetime import datetime, timezone

API = "https://api.x.com/2"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "..", "dashboard", "x_news.json")
NEWS_FIELDS = "id,name,summary,hook,category,contexts,updated_at,cluster_posts_results"
POSTS_PER_STORY = 4  # cluster posts looked up per story, for engagement numbers


def get(path, params, token, timeout=15):
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "lofi-news-radio/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def load_raw(path):
    """First JSON object in the file: plain API JSON, or a tool result with text around it."""
    s = open(path, encoding="utf-8").read()
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", s):
        try:
            obj, _ = dec.raw_decode(s[m.start():])
            if isinstance(obj, dict) and ("data" in obj or "errors" in obj):
                return obj
        except ValueError:
            continue
    raise SystemExit(f"no X API JSON found in {path}")


def iso_z(s):
    return s.replace("+00:00", "Z") if isinstance(s, str) else s


def cluster_ids(story):
    seen = []
    for p in story.get("cluster_posts_results") or []:
        pid = p.get("post_id")
        if pid and pid not in seen:
            seen.append(pid)
    return seen


def to_story(st, posts, users):
    ctx = st.get("contexts") or {}
    ent = ctx.get("entities") or {}
    ids = cluster_ids(st)
    out = {
        "title": (st.get("name") or "").strip(),
        "source": "X",
        "url": st.get("url") or f"https://x.com/i/trending/{st['id']}",
        "domain": "x.com",
        "x_id": st["id"],
    }
    for k in ("hook", "summary", "category"):
        if st.get(k):
            out[k] = st[k].strip()
    if ctx.get("topics"):
        out["topics"] = ctx["topics"]
    entities = [e for k in ("organizations", "people", "products", "events", "places") for e in ent.get(k) or []]
    if entities:
        out["entities"] = entities
    if st.get("updated_at"):
        out["time"] = iso_z(st["updated_at"])
    if ids:
        out["cluster_posts"] = len(ids)
    # Engagement: the most-liked of the cluster posts we looked up (the News API itself has no counts)
    seen = [posts[i] for i in ids[:POSTS_PER_STORY] if i in posts and posts[i].get("public_metrics")]
    if seen:
        top = max(seen, key=lambda p: p["public_metrics"].get("like_count", 0))
        m, user = top["public_metrics"], users.get(top.get("author_id"), {})
        tp = {"id": top["id"], "url": f"https://x.com/{user['username']}/status/{top['id']}" if user.get("username") else f"https://x.com/i/status/{top['id']}"}
        if user.get("username"):
            tp["username"] = user["username"]
        for src, dst in (("like_count", "likes"), ("repost_count", "reposts"), ("reply_count", "replies"),
                         ("quote_count", "quotes"), ("impression_count", "impressions")):
            if src in m:
                tp[dst] = m[src]
        if top.get("created_at"):
            tp["created_at"] = iso_z(top["created_at"])
        out["top_post"] = tp
        out["posts_checked"] = len(seen)
    return out


def build(news, posts_doc, topic, n):
    stories = [s for s in news.get("data") or [] if s.get("id") and s.get("name")]
    if topic:
        stories = [s for s in stories if topic in ((s.get("contexts") or {}).get("topics") or [])]
    stories = stories[:n]
    posts = {p["id"]: p for p in (posts_doc or {}).get("data") or []}
    users = {u["id"]: u for u in ((posts_doc or {}).get("includes") or {}).get("users") or []}
    return [to_story(s, posts, users) for s in stories]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-q", "--query", default="AI technology")
    ap.add_argument("-n", type=int, default=15, help="max stories (default 15)")
    ap.add_argument("--max-age", type=int, default=24, help="max story age in hours, 1-720 (default 24)")
    ap.add_argument("--topic", default="Technology", help='keep stories tagged with this X topic ("" keeps all)')
    ap.add_argument("--from-raw", metavar="NEWS_JSON", help="build from a saved /2/news/search response instead of calling the API")
    ap.add_argument("--posts-raw", metavar="POSTS_JSON", help="saved /2/tweets response with public_metrics (used with --from-raw)")
    ap.add_argument("--via", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--out", default=DEFAULT_OUT, help="output path, or - for stdout")
    a = ap.parse_args()

    if a.from_raw:
        news = load_raw(a.from_raw)
        posts_doc = load_raw(a.posts_raw) if a.posts_raw else None
        via = a.via or "saved X API responses"
    else:
        token = os.environ.get("X_BEARER_TOKEN", "").strip()
        if not token:
            print("error: set X_BEARER_TOKEN (or use --from-raw); x_news.json left unchanged", file=sys.stderr)
            return 1
        try:
            news = get("news/search", {"query": a.query, "max_results": min(100, max(1, a.n * 2)),
                                       "max_age_hours": a.max_age, "news.fields": NEWS_FIELDS}, token)
            ids = []
            for s in (news.get("data") or [])[: a.n * 2]:
                ids += [i for i in cluster_ids(s)[:POSTS_PER_STORY] if i not in ids]
            posts_doc = None
            if ids:
                try:
                    posts_doc = get("tweets", {"ids": ",".join(ids[:100]), "tweet.fields": "public_metrics,created_at,author_id",
                                               "expansions": "author_id", "user.fields": "username"}, token)
                except Exception as e:  # engagement is optional
                    print(f"warn: post lookup failed ({e}); writing stories without engagement", file=sys.stderr)
        except Exception as e:
            print(f"error: couldn't reach the X API ({e}); x_news.json left unchanged", file=sys.stderr)
            return 1
        via = "X API v2"

    stories = build(news, posts_doc, a.topic, a.n)
    if not stories:
        print("error: no stories returned; x_news.json left unchanged", file=sys.stderr)
        return 1
    doc = {
        "source": "X",
        "feed": f"{API}/news/search",
        "query": a.query,
        "max_age_hours": a.max_age,
        "topic_filter": a.topic or None,
        "fetched_via": via,
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "disclaimer": next((s.get("disclaimer") for s in news.get("data") or [] if s.get("disclaimer")), None),
        "stories": stories,
    }
    doc = {k: v for k, v in doc.items() if v is not None}
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    if a.out == "-":
        sys.stdout.write(text)
        return 0
    out = os.path.abspath(a.out)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, out)  # atomic: the dashboard never reads a half-written file
    print(f"wrote {len(stories)} X news stories to {os.path.relpath(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
