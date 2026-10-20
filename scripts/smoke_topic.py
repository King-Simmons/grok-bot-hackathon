#!/usr/bin/env python3
"""Smoke-test Learn a topic for source=web (network) and source=x (cached snapshot).

Run from the repo root:  .venv/bin/python scripts/smoke_topic.py
"""
import asyncio, importlib.util, inspect, json, os, sys, threading, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

os.environ.pop("X_BEARER_TOKEN", None)

from engine.topic_web import (  # noqa: E402
    DISCOVERY_UA, BadSource, Cand, EmptyTopic, gather_pages, normalize_source,
)
from engine import pipeline  # noqa: E402
from engine.server import Handler  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("ok  " if ok else "FAIL") + "  " + name + (("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


def fake_ingest(url):
    if "blocked.example" in url:
        return {"title": "Blocked Example Story", "url": url, "method": "url-words", "words": 4,
                "note": "The site refused the fetch, so this mood is from the words in the link.",
                "text": "blocked example story words"}
    return {"title": "Lo-fi music", "url": url, "method": "trafilatura", "words": 40,
            "text": " ".join(["lofi music"] * 20)}


def test_gather():
    pages = gather_pages([
        Cand("https://en.wikipedia.org/wiki/Lofi", "Lo-fi music", "Wikipedia"),
        Cand("https://blocked.example/news/story", "Blocked Example Story", "News"),
    ], ingest_fn=fake_ingest)
    methods = {p["method"] for p in pages}
    noted = [p for p in pages if p.get("method") == "url-words" and p.get("note")]
    check("gather keeps a real page and a url-words page", "trafilatura" in methods and bool(noted), str(methods))
    check("discovery UA is identifying", "Mozilla" not in DISCOVERY_UA and "TopicLofi" in DISCOVERY_UA)


def test_errors():
    for raw in ("", "   ", "???"):
        try:
            pipeline.synthesize_topic(raw, report_status=False)
            check(f"empty topic {raw!r}", False, "no error")
        except EmptyTopic as e:
            check(f"empty topic {raw!r}", "topic" in str(e).lower(), str(e))
    try:
        normalize_source("fm")
        check("bad source", False, "no error")
    except BadSource as e:
        check("bad source", "web" in str(e).lower(), str(e))


def test_x_cache():
    r = pipeline.synthesize_topic("SpaceX", source="x", report_status=False)
    tp = r["topic"]
    track = r["track"]["path"]
    check("x cache origin", tp.get("source") == "x_topics.json" and tp.get("requested_source") == "x",
          tp.get("source"))
    check("x cache posts", int(tp.get("post_count") or 0) >= 2, str(tp.get("post_count")))
    check("x cache track", os.path.isfile(track), track)
    try:
        pipeline.synthesize_topic("zzz-not-a-cached-topic", source="x", report_status=False)
        check("unknown x topic", False, "no error")
    except pipeline.TopicNotFound as e:
        check("unknown x topic", "x_topics.json" in str(e), str(e)[:80])


def test_web():
    r = pipeline.synthesize_topic("lofi", source="web", report_status=False)
    tp = r["topic"]
    pages = tp.get("pages") or []
    wiki = [p for p in pages if "wikipedia.org" in (p.get("url") or "") and p.get("method") != "url-words"]
    attrs = r["mood"]["musical_attributes"]
    check("web source", tp.get("source") == "web" and tp.get("requested_source") == "web", tp.get("origin"))
    check("web pages", len(pages) >= 2 and all(str(p.get("url", "")).startswith("http") for p in pages),
          ", ".join(p.get("url", "") for p in pages))
    check("web wikipedia text", bool(wiki), str([(p.get("url"), p.get("method")) for p in pages]))
    check("web mood", bool(attrs.get("bpm")) and bool(attrs.get("key")), f"{attrs.get('bpm')} {attrs.get('key')}")
    check("web track", os.path.isfile(r["track"]["path"]), r["track"]["file"])
    print("pages:")
    for p in pages:
        print(f"  - {p.get('via')}  {p.get('method')}  {p.get('words')}w  {p.get('title')}  {p.get('url')}")


def http_json(base, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def test_http():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        code, body = http_json(base, "POST", "/api/synthesize_topic", {})
        check("http empty object", code == 400 and body.get("error_kind") == "empty_topic"
              and "topic" in body.get("error", "").lower(), f"{code} {body}")
        code, body = http_json(base, "POST", "/api/synthesize_topic", {"topic": "   "})
        check("http blank topic", code == 400 and body.get("error_kind") == "empty_topic", f"{code} {body}")
        code, body = http_json(base, "POST", "/api/synthesize_topic", {"topic": "lofi", "source": "fm"})
        check("http bad source", code == 400 and body.get("error_kind") == "bad_source", f"{code} {body}")
        code, body = http_json(base, "POST", "/api/synthesize_topic",
                               {"topic": "zzz-not-a-cached-topic", "source": "x"})
        check("http unknown x", code == 404 and body.get("error_kind") == "topic_not_found", f"{code} {body}")
        code, body = http_json(base, "GET", "/api/topics")
        check("http topics catalog", code == 200 and body.get("default_source") == "web"
              and "web" in (body.get("sources") or []) and bool(body.get("topics")),
              f"default={body.get('default_source')} n={len(body.get('topics') or [])}")
        code, body = http_json(base, "POST", "/api/synthesize_topic", {"topic": "SpaceX", "source": "x"})
        tp = body.get("topic") or {}
        check("http x cache", code == 200 and tp.get("source") == "x_topics.json" and bool(body.get("track_url")),
              f"{code} {tp.get('source')} {body.get('track_url')}")
    finally:
        httpd.shutdown()


def test_mcp():
    path = os.path.join(ROOT, "mcp", "lofi_mcp_server.py")
    spec = importlib.util.spec_from_file_location("lofi_mcp_server_smoke", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sig = inspect.signature(mod.synthesize_topic)
    default = sig.parameters["source"].default
    check("mcp source default", default == "web", repr(default))
    from mcp.server.mcpserver.exceptions import ToolError

    async def run():
        try:
            await mod.synthesize_topic("")
            return "empty-no-error", ""
        except ToolError as e:
            empty = str(e)
        try:
            await mod.synthesize_topic("lofi", source="fm")
            return empty, "bad-no-error"
        except ToolError as e:
            return empty, str(e)

    empty, bad = asyncio.run(run())
    check("mcp empty topic", "topic" in empty.lower() and empty != "empty-no-error", empty[:80])
    check("mcp bad source", "web" in bad.lower() and bad != "bad-no-error", bad[:80])


def main():
    test_gather()
    test_errors()
    test_x_cache()
    test_web()
    test_http()
    test_mcp()
    print(f"\n{len(FAILS)} failed" if FAILS else "\nall checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
