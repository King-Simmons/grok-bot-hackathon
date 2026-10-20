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
    if page.get("note"): meta["note"] = page["note"]
    _mood_cache[url] = (time.time(), mood, debug, meta)
    return mood, debug, meta, {"ingest_s": round(t1 - t0, 2), "mood_s": round(t2 - t1, 2), "cached": False}


def track_url(rel):
    return "/" + rel.replace(os.sep, "/")


def synthesize(url, force=False, progress=None, report_status=True, vocals="none", script=None, agent="anonymous"):
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
        result = {"mood": mood, "track": {"file": rel_mp3, "url": track_url(rel_mp3), "path": mp3, "title": side["title"],
                                        "duration_s": render.get("duration_s")},
                "page": page, "analysis": debug, "timing": timing}
        artifact = rel_mp3
        if str(vocals or "none").strip().lower() in ("summary", "song"):
            from engine.vocals import apply as apply_vocals
            result = apply_vocals(result, vocals, script, agent, name)
            if (result.get("vocals") or {}).get("state") == "ready":
                artifact = result["track"]["file"]
        if report_status:
            _status(state="done", task=f"Made {attrs['bpm']} BPM {attrs['key']} lofi from {page['title'][:60] or url[:60]} ({total}s)",
                    progress=100, artifact=artifact)
        else:
            subprocess.run(["python3", os.path.join(ROOT, "dashboard", "botstatus.py"), "refresh"], cwd=ROOT, capture_output=True, timeout=10)
        return result
    except Exception as e:
        if report_status:
            _status(state="error", task=f"Failed on {url[:80]}", error=str(e)[:200], no_progress=True)
        raise


# ---------------------------------------------------------------------------------------------------------------
# Text / topic -> lofi  ("Learn a topic, via X, to lofi")
# ---------------------------------------------------------------------------------------------------------------
TOPICS_FILE = os.path.join(ROOT, "x_topics.json")
X_RECENT_SEARCH = "https://api.twitter.com/2/tweets/search/recent"
MAX_POSTS = 25


class TopicNotFound(Exception):
    """No posts for this topic: not passed in, no X_BEARER_TOKEN, and not in x_topics.json."""


def clean_post(t):
    """Plain post text for mood analysis: drop links, @handles, RT prefixes and extra whitespace."""
    import unicodedata, html as _html
    t = unicodedata.normalize("NFKC", _html.unescape(str(t or "")))
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"^RT\s+@\w+:\s*", "", t)
    t = re.sub(r"(?<!\w)@\w{1,15}", "", t)
    t = re.sub(r"#(\w+)", r"\1", t)
    t = re.sub(r"(?<!\.)\.\s+\.(?!\.)", ".", t)
    return re.sub(r"\s+", " ", t).strip(" -–—:")


def posts_to_text(posts):
    """One post per line, each ending in punctuation so mood.summarize/_sentences treat them as sentences."""
    out, seen = [], set()
    for p in posts:
        c = clean_post(p)
        if len(c.split()) < 3 or c.lower() in seen:
            continue
        seen.add(c.lower())
        out.append(c if c.endswith((".", "!", "?", "…")) else c + ".")
    return out


def x_search_url(topic):
    import urllib.parse
    return "https://x.com/search?q=" + urllib.parse.quote(topic) + "&f=live"


def _topic_key(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower().lstrip("#")).strip()


def load_topics():
    try:
        with open(TOPICS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"topics": [], "trends": {}}


def cached_topic(topic):
    k = _topic_key(topic)
    data = load_topics()
    for t in data.get("topics", []):
        names = [t.get("topic", "")] + list(t.get("aliases", []))
        if any(_topic_key(n) == k for n in names):
            return t
    return None


def live_x_posts(topic, token=None, max_results=MAX_POSTS):
    """Official X API v2 recent search (last 7 days). Needs X_BEARER_TOKEN. Returns list of post texts."""
    import urllib.parse, urllib.request, urllib.error
    from engine.ingest import _SSL_CTX
    token = token or os.environ.get("X_BEARER_TOKEN")
    if not token:
        return None
    q = f"{topic} -is:retweet lang:en"
    url = X_RECENT_SEARCH + "?" + urllib.parse.urlencode({
        "query": q,
        "max_results": max(10, min(100, max_results)),
        "tweet.fields": "created_at,public_metrics",
    })
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "LofiNewsRadio/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=12, context=_SSL_CTX) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise IngestError(f"X API search failed (HTTP {e.code}); check X_BEARER_TOKEN and your X API plan")
    except Exception as e:
        raise IngestError(f"X API search failed: {e}")
    return [p.get("text", "") for p in data.get("data", [])]


def synthesize_text(title, text, source_url, kind="text", force=False, progress=None, report_status=True,
                    extra=None, label=None, notes=None, vocals="none", script=None, agent="anonymous"):
    """Mood + render from arbitrary text (no fetching). Cache name tracks/<kind-prefix>-<slug>-<hash>.mp3."""
    def step(stage, pct, task):
        if progress: progress(stage, pct)
        if report_status: _status(state="running", task=task, progress=pct, no_error=True, no_artifact=True)

    t0 = time.time()
    if kind.startswith("x"):
        prefix = "x"
    elif kind.startswith("web"):  # topic web search (engine/topic_web.py)
        prefix = "web"
    else:
        prefix = re.sub(r"[^a-z0-9]+", "", kind.lower())[:8] or "text"
    try:
        if not str(text or "").strip():
            raise IngestError("no text to analyze")
        step("analyzing", 30, f"Reading the mood of {title[:60]}")
        mood, debug = analyze(source_url, title, text)
        t1 = time.time()
        attrs = mood["musical_attributes"]
        step("analyzing", 40, f"Mood: {', '.join(attrs['mood_tags'][:2])} · {attrs['bpm']} BPM {attrs['key']}")
        h = hashlib.sha1((kind + "|" + title + "|" + text + json.dumps(attrs, sort_keys=True)).encode()).hexdigest()[:8]
        name = f"{prefix}-{slugify(title, 28)}-{h}"
        rel_mp3 = os.path.join("tracks", name + ".mp3")
        mp3 = os.path.join(ROOT, rel_mp3)
        cached = os.path.exists(mp3) and os.path.exists(mp3[:-4] + ".json") and not force
        if cached:
            with open(mp3[:-4] + ".json") as f:
                side = json.load(f)
            render = side.get("render", {})
        else:
            step("rendering", 55, f"Rendering {attrs['bpm']} BPM {attrs['key']} lofi for {title[:50]}")
            from engine.synth import render as synth_render
            with RENDER_LOCK:
                render = synth_render(attrs, seed=int(h, 16) % 100000, out_mp3=mp3, mood_hint=mood["sentiment"].split(" ")[0])
            side_title = f"X Lofi: {title[:80]}" if prefix == "x" else (
                f"Topic Lofi: {title[:80]}" if prefix == "web" else f"Text Lofi: {title[:80]}")
            side = {"title": side_title,
                    "label": label or (f"X · {title[:18]}" if prefix == "x" else f"{prefix} · {title[:16]}"),
                    "bpm": attrs["bpm"], "key": attrs["key"], "duration_s": render["duration_s"],
                    "notes": notes or "Original lofi generated from the mood of a piece of text (deterministic, no samples).",
                    "source_url": source_url, "kind": kind, "mood_tags": attrs["mood_tags"],
                    "instruments": attrs["primary_instruments"],
                    "created": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                    "mood": mood, "render": render}
            if extra: side.update(extra)
            tmp = mp3[:-4] + ".json.part"
            with open(tmp, "w") as f: json.dump(side, f, indent=2, ensure_ascii=False)
            os.replace(tmp, mp3[:-4] + ".json")
        if progress: progress("finishing", 95)
        total = round(time.time() - t0, 2)
        timing = {"ingest_s": 0.0, "mood_s": round(t1 - t0, 2), "render_s": render.get("render_s"),
                  "encode_s": render.get("encode_s"), "total_s": total, "track_cached": cached}
        page = {"title": title, "url": source_url, "requested_url": source_url, "words": len(text.split()),
                "method": kind, "truncated": False}
        result = {"mood": mood, "track": {"file": rel_mp3, "url": track_url(rel_mp3), "path": mp3, "title": side["title"],
                                        "duration_s": render.get("duration_s")},
                "page": page, "analysis": debug, "timing": timing}
        artifact = rel_mp3
        if str(vocals or "none").strip().lower() in ("summary", "song"):
            from engine.vocals import apply as apply_vocals
            result = apply_vocals(result, vocals, script, agent, name)
            if (result.get("vocals") or {}).get("state") == "ready":
                artifact = result["track"]["file"]
        if report_status:
            _status(state="done", task=f"Made {attrs['bpm']} BPM {attrs['key']} lofi from {title[:60]} ({total}s)",
                    progress=100, artifact=artifact)
        else:
            subprocess.run(["python3", os.path.join(ROOT, "dashboard", "botstatus.py"), "refresh"],
                           cwd=ROOT, capture_output=True, timeout=10)
        return result
    except Exception as e:
        if report_status:
            _status(state="error", task=f"Failed on {title[:80]}", error=str(e)[:200], no_progress=True)
        raise


def resolve_topic_posts(topic, posts=None):
    """-> (posts, source, meta). Order: passed-in posts, live X API (X_BEARER_TOKEN), x_topics.json."""
    topic = str(topic or "").strip()
    if not topic:
        raise IngestError("missing topic")
    if posts:
        return [str(p) for p in posts][:100], "provided", {}
    live_err = None
    if os.environ.get("X_BEARER_TOKEN"):
        try:
            live = live_x_posts(topic)
            if live:
                return live, "x-api-live", {
                    "fetched_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                    "x_reads": len(live),
                }
        except IngestError as e:
            live_err = e
    c = cached_topic(topic)
    if c and c.get("posts"):
        return c["posts"], "x_topics.json", {k: c.get(k) for k in ("fetched_at", "headline", "x_search_url", "topic") if c.get(k)}
    if live_err:
        raise live_err
    raise TopicNotFound(
        f"No posts for \"{topic}\" yet: live X search needs X_BEARER_TOKEN, or ask your Grok Bot to "
        f"fetch it (search X for the topic and call synthesize_topic with the post texts, or refresh "
        f"x_topics.json with engine/x_topics_snapshot.py).")


def synthesize_topic(topic, posts=None, force=False, progress=None, report_status=True, source="web",
                     vocals="none", script=None, agent="anonymous"):
    """Topic -> mood -> original lofi.

    source is 'web' (default: Wikipedia, news RSS, Hacker News via engine/topic_web.py)
    or 'x' (public X posts: live API if X_BEARER_TOKEN is set, else x_topics.json).
    posts is only used when source is 'x'.
    vocals defaults to none and does nothing when Chatterbox is missing.
    """
    from engine.topic_web import normalize_source, normalize_topic, synthesize_web
    source = normalize_source(source)
    if source != "x":
        return synthesize_web(topic, force=force, progress=progress, report_status=report_status,
                              vocals=vocals, script=script, agent=agent)
    # --- X source (optional). The block below is the X-posts path. ---
    # Passed-in posts with a blank topic stay the route's "custom posts" case.
    if not posts:
        topic = normalize_topic(topic)
    if progress: progress("searching", 10)
    raw, source, meta = resolve_topic_posts(topic, posts)
    lines = posts_to_text(raw)
    if len(lines) < 2:
        raise IngestError(f"found only {len(lines)} usable post(s) about \"{topic}\"; need at least 2")
    name = meta.get("topic") or str(topic).strip()
    text = "\n".join(lines)
    surl = meta.get("x_search_url") or x_search_url(name)
    r = synthesize_text(name, text, surl, kind="x-topic", force=force, progress=progress, report_status=report_status,
                        extra={"topic": name, "post_source": source, "post_count": len(lines),
                               "posts_fetched_at": meta.get("fetched_at")},
                        notes=f"Original lofi whose mood comes from {len(lines)} recent public X posts about {name} "
                              f"(deterministic, no samples).",
                        vocals=vocals, script=script, agent=agent)
    r["mood"]["page_summary"] = summarize_posts(lines) or r["mood"]["page_summary"]
    r["topic"] = {"topic": name, "post_count": len(lines), "source": source, "requested_source": "x",
                  "fetched_at": meta.get("fetched_at"),
                  "headline": meta.get("headline"), "x_search_url": surl, "x_reads": int(meta.get("x_reads") or 0),
                  "note": f"mood from {len(lines)} posts on X about {name}"}
    return r


def _sayable(line):
    """A post reads like a sentence: mostly letters, few emoji/symbols, not a list/stat dump or promo."""
    words = line.split()
    if len(words) < 7: return False
    letters = sum(c.isalpha() or c in " ,.'’!?-" for c in line)
    if letters / max(1, len(line)) < 0.86: return False
    if line.count(":") > 2 or line.count("|") or line.count("•") > 1 or line.count("·") > 1 or line.count("/") > 2 or line.count(" - ") > 1: return False
    if re.search(r"\b(subscribe|register|follow us|link in bio|show more|discount|promo code|\d+% off)\b", line, re.I): return False
    return True


def summarize_posts(lines, max_chars=300, n=3):
    """'What people are saying': the first sentence of up to n different readable posts."""
    from engine.mood import _sentences, summarize
    picked = []
    for line in lines:
        if not _sayable(line): continue
        sents = _sentences(line) or [line]
        s1, k = sents[0], 1
        while k < len(sents) and (len(s1.split()) < 6 or re.search(r"\b(No|Dr|Mr|Mrs|Ms|St|vs|Jr|Sr|U\.S)\.$", s1)):
            s1 += " " + sents[k]; k += 1
        if len(s1) > 140: s1 = s1[:140].rsplit(" ", 1)[0].rstrip(",;:-") + "…"
        if not s1.endswith((".", "!", "?", "…")): s1 += "."
        picked.append(s1)
        if len(picked) >= n or len(" ".join(picked)) >= max_chars: break
    if not picked:
        return summarize("\n".join(lines), max_chars=max_chars, n=n)
    return " ".join(picked)
