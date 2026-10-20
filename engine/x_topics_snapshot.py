#!/usr/bin/env python3
"""engine/x_topics_snapshot.py - merge raw X search dumps into x_topics.json (the engine's topic cache).

The engine can't call a Grok Bot's X tools at runtime, so a bot refreshes this cache:
  1. search X for a topic (x MCP `search_posts_all`, query '<topic> -is:retweet lang:en since:YYYY-MM-DD')
     and save the raw JSON result to a file
  2. python3 engine/x_topics_snapshot.py add "AI agents" raw.json [--headline "..."] [--alias "agents"]
  3. optional trend suggestions:  python3 engine/x_topics_snapshot.py trends raw_trends.json [--woeid 23424977]

Raw files may be the plain API JSON ({"data":[{"text":...}, ...]}) or a tool result with text around it;
the first JSON object/array in the file is used. Accepted post shapes: {"text": ...}, {"full_text": ...}, plain strings.
Only plain post text is kept (links/@handles stripped by the engine at render time); no usernames are stored.
Also: `list` prints the cache, `remove <topic>` drops one.
"""
import argparse, datetime, html, json, os, re, sys, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "x_topics.json")
MAX_POSTS = 25


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def load_raw(path):
    s = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
    dec = json.JSONDecoder()
    for m in re.finditer(r"[\[{]", s):
        try:
            obj, _ = dec.raw_decode(s[m.start():])
            if isinstance(obj, (dict, list)):
                return obj
        except ValueError:
            continue
    raise SystemExit(f"no JSON found in {path}")


def _items(obj):
    if isinstance(obj, list): return obj
    for k in ("data", "posts", "results", "items", "statuses"):
        if isinstance(obj.get(k), list): return obj[k]
    return []


def scrub(t):
    t = re.sub(r"https?://\S+", "", html.unescape(str(t)))
    t = re.sub(r"(?<!\w)@\w{1,15}", "", t)  # no usernames in the cache
    return re.sub(r"\s+", " ", t).strip()


# Demo-safety filter: drop explicit, abusive or spammy posts (the cache feeds a public dashboard).
BLOCK = re.compile(r"\b(fuck\w*|shit\w*|cunt\w*|bitch\w*|whore|slut|porn\w*|nsfw|onlyfans|breed|nude\w*|sex\w*|transe?xual\w*|"
                   r"idi\W?t|retard\w*|n[i1]gg\w*|f[a4]gg?\w*|pump\.?fun|airdrop|giveaway)\b|[1-9A-HJ-NP-Za-km-z]{32,}", re.I)


def ok_post(t):
    if BLOCK.search(t): return False
    words = t.lower().split()
    return len(set(words)) >= 0.4 * len(words)  # drop copy-paste repetition spam


def posts_from(obj):
    out, seen = [], set()
    for it in _items(obj):
        t = it if isinstance(it, str) else (it.get("text") or it.get("full_text") or it.get("note_tweet", {}).get("text") or "")
        t = scrub(t)
        if len(t.split()) >= 3 and t.lower() not in seen and ok_post(t):
            seen.add(t.lower()); out.append(t)
    return out[:MAX_POSTS]


def load():
    try:
        with open(OUT, encoding="utf-8") as f: return json.load(f)
    except Exception:
        return {"topics": [], "trends": {}}


def save(d):
    d["updated"] = now_iso()
    d.setdefault("about", "Cache of recent public X posts per topic for 'Learn a topic, via X, to lofi'. "
                          "Refresh with engine/x_topics_snapshot.py (see README).")
    tmp = OUT + ".part"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(d, f, indent=2, ensure_ascii=False)
    os.replace(tmp, OUT)


def key(s): return re.sub(r"[^a-z0-9]+", " ", s.lower().lstrip("#")).strip()


def cmd_add(a):
    posts = posts_from(load_raw(a.raw))
    if not posts: raise SystemExit("no usable posts in the dump")
    d = load()
    entry = {"topic": a.topic, "aliases": a.alias or [], "fetched_at": a.fetched_at or now_iso(),
             "query": a.query or f"{a.topic} -is:retweet lang:en",
             "x_search_url": "https://x.com/search?q=" + urllib.parse.quote(a.topic) + "&f=live",
             "headline": a.headline, "post_count": len(posts), "posts": posts}
    d["topics"] = [t for t in d.get("topics", []) if key(t.get("topic", "")) != key(a.topic)] + [entry]
    save(d); print(f"{a.topic}: {len(posts)} posts -> {OUT}")


def cmd_trends(a):
    obj = load_raw(a.raw); items, seen = [], set()
    for it in _items(obj):
        name = it.get("trend_name") or it.get("name") if isinstance(it, dict) else None
        if not name or name in seen: continue
        seen.add(name)
        items.append({"name": name, "url": it.get("url") or "https://x.com/search?q=" + urllib.parse.quote(name),
                      "post_count": it.get("tweet_count") or it.get("post_count")})
    d = load(); d["trends"] = {"as_of": a.fetched_at or now_iso(), "woeid": a.woeid, "items": items[:a.max]}
    save(d); print(f"{len(items[:a.max])} trends -> {OUT}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add"); p.add_argument("topic"); p.add_argument("raw", help="raw search JSON file, or - for stdin")
    p.add_argument("--headline"); p.add_argument("--alias", action="append"); p.add_argument("--query"); p.add_argument("--fetched-at")
    p.set_defaults(fn=cmd_add)
    p = sub.add_parser("trends"); p.add_argument("raw"); p.add_argument("--woeid", type=int, default=23424977)
    p.add_argument("--max", type=int, default=12); p.add_argument("--fetched-at"); p.set_defaults(fn=cmd_trends)
    p = sub.add_parser("list"); p.set_defaults(fn=lambda a: [print(f"{t['topic']:<24} {t['post_count']:>3} posts  {t['fetched_at']}") for t in load().get("topics", [])])
    p = sub.add_parser("remove"); p.add_argument("topic")
    p.set_defaults(fn=lambda a: (lambda d: (d.update(topics=[t for t in d["topics"] if key(t["topic"]) != key(a.topic)]), save(d)))(load()))
    a = ap.parse_args(); a.fn(a)


if __name__ == "__main__":
    main()
