#!/usr/bin/env python3
"""Pull top stories from the official Hacker News API into dashboard/headlines.json.

Zero dependencies (stdlib only). Usage, from the repo root:

    python3 scripts/fetch_hn.py              # 15 top stories
    python3 scripts/fetch_hn.py -n 25        # more
    python3 scripts/fetch_hn.py --list best  # top | new | best
    python3 scripts/fetch_hn.py --out -      # print instead of writing

API docs: https://github.com/HackerNews/API
"""
import argparse, json, os, sys, tempfile, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlparse

API = "https://hacker-news.firebaseio.com/v0"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "..", "dashboard", "headlines.json")


def get(path, timeout=10):
    req = urllib.request.Request(f"{API}/{path}", headers={"User-Agent": "lofi-news-radio/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def domain_of(url):
    host = urlparse(url or "").hostname or ""
    return host[4:] if host.startswith("www.") else host


def to_headline(item):
    hn_id = item["id"]
    discussion = f"https://news.ycombinator.com/item?id={hn_id}"
    url = item.get("url") or discussion  # Ask HN / Show HN text posts have no url
    return {
        "title": item.get("title", "").strip(),
        "source": "Hacker News",
        "url": url,
        "domain": domain_of(item.get("url")) or "news.ycombinator.com",
        "score": item.get("score", 0),
        "by": item.get("by", ""),
        "time": datetime.fromtimestamp(item.get("time", 0), timezone.utc).isoformat().replace("+00:00", "Z"),
        "comments": item.get("descendants", 0),
        "hn_id": hn_id,
        "discussion": discussion,
    }


def fetch(n=15, which="top"):
    ids = get(f"{which}stories.json")[: n * 2]  # over-fetch: some items are jobs/dead/deleted
    with ThreadPoolExecutor(max_workers=8) as ex:
        items = list(ex.map(lambda i: _safe_item(i), ids))
    stories = [it for it in items if it and it.get("type") == "story" and it.get("title")
               and not it.get("dead") and not it.get("deleted")]
    return [to_headline(it) for it in stories[:n]]


def _safe_item(i):
    try:
        return get(f"item/{i}.json")
    except Exception as e:  # one bad item shouldn't sink the run
        print(f"warn: item {i}: {e}", file=sys.stderr)
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-n", type=int, default=15, help="number of stories (default 15)")
    ap.add_argument("--list", default="top", choices=["top", "new", "best"])
    ap.add_argument("--out", default=DEFAULT_OUT, help="output path, or - for stdout")
    a = ap.parse_args()
    try:
        heads = fetch(a.n, a.list)
    except Exception as e:
        print(f"error: couldn't reach the Hacker News API ({e}); headlines.json left unchanged", file=sys.stderr)
        return 1
    if not heads:
        print("error: no stories returned; headlines.json left unchanged", file=sys.stderr)
        return 1
    doc = {
        "source": "Hacker News",
        "feed": f"{API}/{a.list}stories.json",
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "headlines": heads,
    }
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    if a.out == "-":
        sys.stdout.write(text)
        return 0
    out = os.path.abspath(a.out)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, out)  # atomic: the dashboard never reads a half-written file
    print(f"wrote {len(heads)} Hacker News stories to {os.path.relpath(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
