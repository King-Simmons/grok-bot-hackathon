"""engine/pipeline.py - URL -> ingest -> mood JSON -> synth mp3 (+ sidecar) -> tracks.json.

Used by engine/server.py (HTTP API) and mcp/lofi_mcp_server.py (MCP tools).
Reports progress to the dashboard via botstatus.py as id 'url-lofi'.
"""
import hashlib, json, os, re, subprocess, sys, threading, time, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine.ingest import ingest, IngestError  # noqa: E402
from engine.mood import analyze  # noqa: E402

TRACK_DIR = os.path.join(ROOT, "tracks")
RENDER_LOCK = threading.Lock()  # one render at a time (CPU-bound)
_mood_cache = {}  # url -> (ts, mood, debug, page)
CACHE_S = 600


def _status(**kw):
    """Best-effort botstatus update (never breaks the pipeline)."""
    args = ["python3", os.path.join(ROOT, "dashboard", "botstatus.py"), "set", "--id", "url-lofi",
            "--name", "URL-to-Lofi", "--owner", "King", "--role", "URL-to-Lofi Synthesizer engine"]
    for k, v in kw.items():
        if v is None: continue
        flag = "--" + k.replace("_", "-")
        args += [flag] if v is True else [flag, str(v)]
    try:
        subprocess.run(args, cwd=ROOT, capture_output=True, timeout=10)
    except Exception:
        pass


def slugify(s, n=32):
    s = re.sub(r"^https?://(www\.)?", "", s.lower())
    return (re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:n].strip("-")) or "page"


def short_label(url):
    """Picker chip text, e.g. 'bbc · cjkgeq0k1wevo' or 'github · python-sdk'."""
    import urllib.parse
    u = urllib.parse.urlparse(url)
    host = (u.hostname or "page").removeprefix("www.").split(".")
    if len(host) >= 3 and host[-2] in ("co", "com"): site = host[-3]
    elif len(host) >= 2: site = ".".join(host[-2:]) if len(host[-2]) <= 2 else host[-2]
    else: site = host[0]
    tail = [x for x in u.path.split("/") if x]
    return f"{site} · {tail[-1][:16]}" if tail else site


def mood_for(url, use_cache=True):
    t0 = time.time()
    c = _mood_cache.get(url)
    if use_cache and c and time.time() - c[0] < CACHE_S:
        return c[1], c[2], c[3], {"ingest_s": 0.0, "mood_s": 0.0, "cached": True}
    page = ingest(url)
    t1 = time.time()
    mood, debug = analyze(page["url"], page["title"], page["text"])
    t2 = time.time()
    meta = {k: page[k] for k in ("title", "url", "requested_url", "words", "method", "truncated")}
    _mood_cache[url] = (time.time(), mood, debug, meta)
    return mood, debug, meta, {"ingest_s": round(t1 - t0, 2), "mood_s": round(t2 - t1, 2), "cached": False}


def track_url(rel):
    return "/" + rel.replace(os.sep, "/")


def synthesize(url, force=False, progress=None, report_status=True):
    """Full pipeline. progress(stage, pct) optional. Returns dict with mood + track info."""
    def step(stage, pct, task):
        if progress: progress(stage, pct)
        if report_status: _status(state="running", task=task, progress=pct, no_error=True, no_artifact=True)

    t0 = time.time()
    try:
        step("fetching", 10, f"Reading {url[:80]}")
        mood, debug, page, timing = mood_for(url)
        attrs = mood["musical_attributes"]
        step("analyzing", 35, f"Mood: {', '.join(attrs['mood_tags'][:2])} · {attrs['bpm']} BPM {attrs['key']}")
        h = hashlib.sha1((page["url"] + json.dumps(attrs, sort_keys=True)).encode()).hexdigest()[:8]
        name = f"url-{slugify(page['url'])}-{h}"
        rel_mp3 = os.path.join("tracks", name + ".mp3")
        mp3 = os.path.join(ROOT, rel_mp3)
        cached = os.path.exists(mp3) and not force
        if cached:
            with open(mp3[:-4] + ".json") as f:
                side = json.load(f)
            render = side.get("render", {})
        else:
            step("rendering", 55, f"Rendering {attrs['bpm']} BPM {attrs['key']} lofi for {page['title'][:50] or url[:50]}")
            from engine.synth import render as synth_render
            with RENDER_LOCK:
                render = synth_render(attrs, seed=int(h, 16) % 100000, out_mp3=mp3, mood_hint=mood["sentiment"].split(" ")[0])
            side = {"title": f"URL Lofi: {page['title'][:80] or page['url']}", "label": short_label(page["url"]),
                    "bpm": attrs["bpm"], "key": attrs["key"], "duration_s": render["duration_s"],
                    "notes": "Original lofi generated from a web page's mood (deterministic, no samples).",
                    "source_url": page["url"], "mood_tags": attrs["mood_tags"], "instruments": attrs["primary_instruments"],
                    "created": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                    "mood": mood, "render": render}
            tmp = mp3[:-4] + ".json.part"
            with open(tmp, "w") as f: json.dump(side, f, indent=2)
            os.replace(tmp, mp3[:-4] + ".json")
        if progress: progress("finishing", 95)
        total = round(time.time() - t0, 2)
        timing.update({"render_s": render.get("render_s"), "encode_s": render.get("encode_s"), "total_s": total, "track_cached": cached})
        if report_status:
            _status(state="done", task=f"Made {attrs['bpm']} BPM {attrs['key']} lofi from {page['title'][:60] or url[:60]} ({total}s)",
                    progress=100, artifact=rel_mp3)
        else:
            subprocess.run(["python3", os.path.join(ROOT, "dashboard", "botstatus.py"), "refresh"], cwd=ROOT, capture_output=True, timeout=10)
        return {"mood": mood, "track": {"file": rel_mp3, "url": track_url(rel_mp3), "path": mp3, "title": side["title"],
                                        "duration_s": render.get("duration_s")},
                "page": page, "analysis": debug, "timing": timing}
    except Exception as e:
        if report_status:
            _status(state="error", task=f"Failed on {url[:80]}", error=str(e)[:200], no_progress=True)
        raise
