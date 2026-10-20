"""engine/ingest.py - fetch a URL and extract title + readable body text.

Uses trafilatura when installed, else a stdlib HTMLParser fallback.
Safety: http(s) only, refuses private/loopback hosts, timeouts, size cap.
"""
import html as htmlmod, ipaddress, re, socket, ssl, urllib.error, urllib.parse, urllib.request
import certifi
from html.parser import HTMLParser

UA = "Mozilla/5.0 (X11; Linux x86_64) LofiNewsRadio-URL2Lofi/1.0 (+hackathon prototype)"
MAX_BYTES = 3_000_000
TIMEOUT = 12
_FAKE_IP_NET = ipaddress.ip_network("198.18.0.0/15")


class IngestError(Exception):
    pass


def _check_host(url):
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise IngestError("only http(s) URLs are supported")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror as e:
        raise IngestError(f"can't resolve host {p.hostname}: {e}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip in _FAKE_IP_NET:  # this box's DNS maps public names into 198.18.0.0/15 (proxy fake-IP)
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise IngestError(f"refusing to fetch private/local address {ip}")


class _NoPrivateRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _ssl_context():
    """CA bundle from certifi. python.org macOS Python does not use the system store,
    so the default context raises CERTIFICATE_VERIFY_FAILED on every HTTPS fetch."""
    return ssl.create_default_context(cafile=certifi.where())


def fetch(url):
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    _check_host(url)
    opener = urllib.request.build_opener(
        _NoPrivateRedirect,
        urllib.request.HTTPSHandler(context=_ssl_context()),
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                                               "Accept-Language": "en-US,en;q=0.8"})
    try:
        with opener.open(req, timeout=TIMEOUT) as r:
            ctype = r.headers.get("Content-Type", "")
            if ctype and not re.search(r"text/|html|xml|json", ctype):
                raise IngestError(f"unsupported content type {ctype}")
            raw = r.read(MAX_BYTES + 1)
            final = r.geturl()
            charset = r.headers.get_content_charset() or "utf-8"
    except IngestError:
        raise
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429):
            raise IngestError(f"the site refused automated readers (HTTP {e.code})") from e
        raise IngestError(f"fetch failed: {e}") from e
    except Exception as e:
        raise IngestError(f"fetch failed: {e}") from e
    truncated = len(raw) > MAX_BYTES
    return final, raw[:MAX_BYTES].decode(charset, errors="replace"), truncated


class _Fallback(HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "button", "template", "iframe"}
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "br", "section", "article", "tr", "pre", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0; self.parts = []; self.title = ""; self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP: self.depth += 1
        if tag == "title": self._in_title = True
        if tag in self.BLOCK: self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth: self.depth -= 1
        if tag == "title": self._in_title = False
        if tag in self.BLOCK: self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title: self.title += data
        elif not self.depth: self.parts.append(data)


def _fallback_extract(html):
    p = _Fallback(); p.feed(html)
    lines = [re.sub(r"\s+", " ", l).strip() for l in "".join(p.parts).split("\n")]
    lines = [l for l in lines if len(l.split()) >= 4]  # drop menu crumbs
    return p.title.strip(), "\n".join(lines)


def extract(html, url=None):
    title, text, method = "", "", "stdlib"
    try:
        import trafilatura
        text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False, favor_recall=True) or ""
        md = trafilatura.extract_metadata(html, default_url=url)
        title = (md.title if md and md.title else "") or ""
        method = "trafilatura"
    except ImportError:
        pass
    if len(text.split()) < 40:  # trafilatura found little (JS-heavy / docs pages): fall back
        t2, x2 = _fallback_extract(html)
        if len(x2.split()) > len(text.split()):
            text, method = x2, method + "+stdlib" if method != "stdlib" else "stdlib"
        title = title or t2
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title = htmlmod.unescape(m.group(1)).strip() if m else ""
    m = re.search(r'<meta[^>]+(?:name|property)=["\'](?:og:)?description["\'][^>]+content=["\']([^"\']+)', html, re.I)
    desc = htmlmod.unescape(m.group(1)).strip() if m else ""
    return {"title": re.sub(r"\s+", " ", title)[:300], "text": text.strip(), "description": desc, "method": method}


def ingest(url):
    final, html, truncated = fetch(url)
    d = extract(html, final)
    if not d["text"] and d["description"]:
        d["text"] = d["description"]
    if not d["text"]:
        raise IngestError("no readable text found on the page")
    d.update({"url": final, "requested_url": url, "truncated": truncated, "words": len(d["text"].split())})
    return d


if __name__ == "__main__":
    import json, sys
    r = ingest(sys.argv[1]); r["text"] = r["text"][:600] + "…"
    print(json.dumps(r, indent=2))
