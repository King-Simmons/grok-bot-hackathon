"""engine/topic_web.py - web source for Learn a topic -> lofi.

Wikipedia, a public news RSS feed, and Hacker News. Each page is read with
engine.ingest, which already turns an HTTP refusal into url-words. Discovery
uses an identifying User-Agent and does not retry with a browser UA.

X posts stay in engine/pipeline.py. This module only collects public pages
and calls pipeline.synthesize_text.
"""
import html, json, re, ssl, urllib.parse, urllib.request
from collections import Counter, namedtuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from engine.ingest import IngestError, ingest

DISCOVERY_UA = ("LofiNewsRadio-TopicLofi/1.0 "
                "(hackathon; keyless public sources; +https://github.com/King-Simmons/grok-bot-hackathon)")
MAX_PAGES = 5
Cand = namedtuple("Cand", "url title via")

_STOP = set("""the a an and or of to in on at by for with from as is are was were be it its this that
    you your we our they their not how what when where why who into about over new one two via www
    http https com org news post posts article articles story""".split())


class TopicWebError(Exception):
    error_kind = "topic_error"
    http_status = 400


class EmptyTopic(TopicWebError):
    error_kind = "empty_topic"


class BadSource(TopicWebError):
    error_kind = "bad_source"


def normalize_source(source):
    """'web' (default) or 'x'. Blank means web."""
    if source is None:
        return "web"
    s = str(source).strip().lower()
    if not s:
        return "web"
    if s not in ("web", "x"):
        raise BadSource('Source must be "web" or "x". Web is the default; X is optional.')
    return s


def normalize_topic(topic):
    topic = re.sub(r"\s+", " ", str(topic or "")).strip()
    if len(topic) > 120:
        topic = topic[:120].rstrip()
    if not topic or not any(c.isalnum() for c in topic):
        raise EmptyTopic('Please enter a topic to learn. Try a short phrase like "lofi" or "space telescopes".')
    return topic


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_SSL = _ssl_context()


def _tokens(s):
    out = []
    for w in re.findall(r"[^\W_]{2,}", (s or "").lower()):
        if w in _STOP:
            continue
        if len(w) > 4 and w.endswith("s"):
            w = w[:-1]
        out.append(w)
    return out


def _compact(s):
    return re.sub(r"[\W_]+", "", (s or "").lower())


def _relevant(topic, title, extra=""):
    """Drop hits that never mention the topic. News RSS sometimes backfills unrelated stories."""
    need_tokens = _tokens(topic)
    if not need_tokens:
        return False
    blob_text = f"{title} {extra}"
    topic_compact = _compact(topic)
    if topic_compact and len(topic_compact) >= 3 and topic_compact in _compact(blob_text):
        return True
    blob = set(_tokens(blob_text))
    hit = sum(1 for t in need_tokens if t in blob)
    need = 1 if len(need_tokens) <= 2 else max(1, (len(need_tokens) + 1) // 2)
    return hit >= need


def _open(url, accept, timeout=10, limit=1_500_000):
    req = urllib.request.Request(url, headers={"User-Agent": DISCOVERY_UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
        return r.read(limit)


def _get_json(url):
    return json.loads(_open(url, "application/json").decode("utf-8", "replace"))


def _dedupe(cands):
    out, seen = [], set()
    for c in cands:
        if not c.url or not str(c.url).lower().startswith("http"):
            continue
        p = urllib.parse.urlparse(c.url)
        key = ((p.hostname or "").lower().removeprefix("www.") + p.path.rstrip("/").lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def wiki_candidates(topic):
    q = {"search": topic, "limit": 5, "namespace": 0, "format": "json"}
    try:
        data = _get_json("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode({"action": "opensearch", **q}))
        found = []
        for title, desc, link in zip(data[1], data[2], data[3]):
            if isinstance(link, str) and link.startswith("http") and _relevant(topic, title, desc or ""):
                found.append(Cand(link, title, "Wikipedia"))
        if found:
            return found
    except Exception:
        pass
    try:
        data = _get_json("https://en.wikipedia.org/w/rest.php/v1/search/page?" + urllib.parse.urlencode({"q": topic, "limit": 5}))
    except Exception:
        return []
    found = []
    for page in data.get("pages") or []:
        key, title = page.get("key") or "", page.get("title") or ""
        desc = re.sub(r"<[^>]+>", "", page.get("description") or page.get("excerpt") or "")
        if key and _relevant(topic, title, desc):
            found.append(Cand("https://en.wikipedia.org/wiki/" + urllib.parse.quote(key), title, "Wikipedia"))
    return found


def wikinews_candidates(topic):
    try:
        data = _get_json("https://en.wikinews.org/w/api.php?" + urllib.parse.urlencode({
            "action": "query", "list": "search", "srsearch": topic, "srlimit": 5,
            "srnamespace": 0, "format": "json", "utf8": 1}))
    except Exception:
        return []
    found = []
    for hit in (data.get("query") or {}).get("search") or []:
        title = hit.get("title") or ""
        snippet = re.sub(r"<[^>]+>", "", hit.get("snippet") or "")
        if not _relevant(topic, title, snippet):
            continue
        slug = urllib.parse.quote(title.replace(" ", "_"), safe="()'!$*,-._~:")
        found.append(Cand("https://en.wikinews.org/wiki/" + slug, title, "Wikinews"))
    return found


def _publisher_url(link):
    """Use the article URL already inside an RSS redirect, not the tracker host."""
    link = html.unescape(link or "").strip()
    if not link.startswith("http"):
        return ""
    p = urllib.parse.urlparse(link)
    host = (p.hostname or "").lower()
    wrapped = urllib.parse.parse_qs(p.query).get("url") or []
    if wrapped and wrapped[0].startswith("http"):
        return wrapped[0]
    if host.endswith("bing.com") or host.endswith("news.google.com"):
        return ""
    return link


def news_rss_candidates(topic):
    """Public Bing News RSS. No API key. Unrelated backfill is dropped."""
    from xml.etree import ElementTree as ET
    url = "https://www.bing.com/news/search?" + urllib.parse.urlencode({"q": topic, "format": "rss"})
    try:
        root = ET.fromstring(_open(url, "application/rss+xml, application/xml, text/xml"))
    except Exception:
        return []
    found = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
        if not _relevant(topic, title, desc):
            continue
        link = _publisher_url(item.findtext("link") or "") or _publisher_url(item.findtext("guid") or "")
        if link:
            found.append(Cand(link, title, "News"))
    return found


def hn_candidates(topic):
    try:
        data = _get_json("https://hn.algolia.com/api/v1/search?" + urllib.parse.urlencode({
            "query": topic, "tags": "story", "hitsPerPage": 8}))
    except Exception:
        return []
    found = []
    for hit in data.get("hits") or []:
        title = hit.get("title") or ""
        if not _relevant(topic, title):
            continue
        link = hit.get("url") or ""
        if not (isinstance(link, str) and link.startswith("http")):
            oid = hit.get("objectID")
            if not oid:
                continue
            link = f"https://news.ycombinator.com/item?id={oid}"
        found.append(Cand(link, title, "Hacker News"))
    return found


def discover_web(topic):
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {
            "wiki": ex.submit(wiki_candidates, topic),
            "wn": ex.submit(wikinews_candidates, topic),
            "rss": ex.submit(news_rss_candidates, topic),
            "hn": ex.submit(hn_candidates, topic),
        }
        got = {}
        for name, fut in futs.items():
            try:
                got[name] = fut.result()
            except Exception:
                got[name] = []
    ordered = got["wiki"][:2] + got["wn"][:1] + got["rss"][:2] + got["hn"][:2]
    return _dedupe(ordered)[:MAX_PAGES]


def _clip_words(text, n):
    words = (text or "").split()
    if len(words) <= n:
        return (text or "").strip()
    return " ".join(words[:n])


def _clean_title(title):
    title = re.sub(r"\s+", " ", title or "").strip()
    title = re.sub(r"\s+[-|–—]\s+Wikipedia$", "", title)
    title = re.sub(r"\s+[-|–—]\s+Wikinews.*$", "", title)
    return title[:180]


def _load_page(cand, ingest_fn):
    try:
        page = ingest_fn(cand.url)
    except IngestError:
        return None
    if not page or not str(page.get("text") or "").strip():
        return None
    if page.get("method") != "url-words" and int(page.get("words") or 0) < 40:
        return None
    title = _clean_title(page.get("title") or cand.title or cand.url)
    return {"title": title or cand.title, "url": page.get("url") or cand.url, "via": cand.via,
            "method": page.get("method") or "", "words": int(page.get("words") or 0),
            "note": page.get("note") or "", "text": page.get("text") or ""}


def gather_pages(candidates, ingest_fn=None):
    """Fetch through ingest. Keep url-words pages when real article text is scarce."""
    ingest_fn = ingest_fn or ingest
    if not candidates:
        return []
    results = [None] * len(candidates)
    with ThreadPoolExecutor(max_workers=min(4, len(candidates))) as ex:
        futs = {ex.submit(_load_page, c, ingest_fn): i for i, c in enumerate(candidates)}
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                results[i] = fut.result()
            except Exception:
                results[i] = None
    pages = [p for p in results if p]
    real = [p for p in pages if p.get("method") != "url-words"]
    weak = [p for p in pages if p.get("method") == "url-words"]
    if len(real) >= 2:
        return real[:MAX_PAGES]
    return (real + weak)[:MAX_PAGES]


def _public_page(p):
    out = {"title": p["title"], "url": p["url"], "via": p["via"], "method": p["method"], "words": p["words"]}
    if p.get("note"):
        out["note"] = p["note"]
    return out


def synthesize_web(topic, force=False, progress=None, report_status=True,
                   vocals="none", script=None, agent="anonymous"):
    """Public pages -> combined text -> the existing text-to-lofi renderer."""
    from engine.pipeline import TopicNotFound, synthesize_text
    topic = normalize_topic(topic)
    if progress:
        progress("searching", 12)
    pages = gather_pages(discover_web(topic))
    if not pages:
        raise TopicNotFound(
            f'I couldn\'t find public pages about "{topic}". Try another topic, or switch the source to X.')
    counts = Counter(p["via"] for p in pages)
    bits = [f"{n} {name}" for name, n in counts.items()]
    blocked = sum(1 for p in pages if p.get("method") == "url-words")
    note = f"Mood from {len(pages)} public pages about {topic}: {', '.join(bits)}."
    if blocked:
        note += f" {blocked} page(s) refused the fetch, so those used the link's words."
    text = _clip_words("\n\n".join(f"{p['title']}.\n{_clip_words(p['text'], 1500)}" for p in pages), 5000)
    public = [_public_page(p) for p in pages]
    if progress:
        progress("fetching", 30)
    r = synthesize_text(
        topic, text, pages[0]["url"], kind="web-topic", force=force, progress=progress,
        report_status=report_status,
        extra={"topic": topic, "source": "web", "requested_source": "web", "origin": "web-search", "pages": public},
        label=f"web · {topic[:16]}",
        notes=note + " Original lofi, deterministic, no samples.",
        vocals=vocals, script=script, agent=agent)
    r["topic"] = {"topic": topic, "source": "web", "requested_source": "web", "origin": "web-search",
                  "pages": public, "page_count": len(public), "x_reads": 0, "note": note}
    r["page"]["method"] = "web-search"
    r["page"]["words"] = len(text.split())
    return r
