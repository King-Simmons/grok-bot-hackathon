"""Interest profile, ranking, trend match, and one news-talk batch.

Typed topics are enough. Likes and following are optional and use a user
token from X OAuth 2.0 PKCE. That token is not X_BEARER_TOKEN. The desk
directory lives outside the repo. This module does not write the HTTP job table.
"""
import base64, hashlib, json, os, re, secrets, threading, time, urllib.parse
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_BATCH = 4
SCOPES = "tweet.read users.read like.read follows.read offline.access"
PKCE_TTL_S = 600
AUTH_URL = "https://twitter.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
X_API = "https://api.x.com/2"
GROK_URL = "https://api.x.ai/v1/chat/completions"
BROAD = {"ai", "technology", "tech", "news", "science", "gaming", "software"}
_STOP = set("""the a an and or of to in on at by for with from as is are was were be it its this that
    you your we our they their not how what when where why who into about over new one two via www
    http https com org news post posts just like liked""".split())

# Tests replace these. None means the real network, renderer, or news file.
TRANSPORT = None
RENDER = None
DISCOVER = None
RANKER = None
NEWS_PATH = None
_HOME = None
_PENDING = {}
_PENDING_LOCK = threading.Lock()
_DESK_GUARD = threading.Lock()
_NOTICE = ""


class ForYouRejected(Exception):
    def __init__(self, message, error_kind, http_status=400):
        super().__init__(message)
        self.error_kind = error_kind
        self.http_status = http_status


class TransportError(Exception):
    def __init__(self, status):
        super().__init__("HTTP %s" % status)
        self.status = int(status)


@dataclass(frozen=True)
class Interest:
    topic: str
    because: str


def use_home(path):
    """Point the desk at a directory. Tests pass a temp dir. None uses the env default."""
    global _HOME
    _HOME = os.path.abspath(path) if path else None


def reset_runtime():
    """Drop login state and test hooks. Does not delete a desk on disk."""
    global _NOTICE, TRANSPORT, RENDER, DISCOVER, RANKER, NEWS_PATH
    with _PENDING_LOCK:
        _PENDING.clear()
    _NOTICE = ""
    TRANSPORT = RENDER = DISCOVER = RANKER = NEWS_PATH = None


def home():
    if _HOME:
        return _HOME
    env = os.environ.get("LOFI_X_HOME", "").strip()
    if env:
        return os.path.abspath(env)
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = xdg if xdg else os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "lofi-news-radio")


def _desk():
    path = home()
    os.makedirs(path, 0o700, exist_ok=True)
    return path


def _read_json(name):
    path = os.path.join(_desk(), name)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(name, data):
    folder = _desk()
    path = os.path.join(folder, name)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _client():
    cid = os.environ.get("X_CLIENT_ID", "").strip()
    secret = os.environ.get("X_CLIENT_SECRET", "").strip()
    return cid, secret


def _topic_key(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _tokens(text):
    out = []
    for word in re.findall(r"[^\W_]{2,}", str(text or "").lower()):
        if word in _STOP:
            continue
        if len(word) > 4 and word.endswith("s"):
            word = word[:-1]
        out.append(word)
    return out


def _normalize_many(values):
    from engine.topic_web import EmptyTopic, normalize_topic
    out = []
    seen = set()
    for raw in values or []:
        try:
            topic = normalize_topic(raw)
        except EmptyTopic as exc:
            raise ForYouRejected(str(exc), "bad_topic") from exc
        key = _topic_key(topic)
        if key in seen:
            continue
        seen.add(key)
        out.append(topic)
    return out


def save_topics(values):
    topics = _normalize_many(values)
    if len(topics) > MAX_BATCH:
        raise ForYouRejected(
            "A batch holds %d topics. Remove one and try again." % MAX_BATCH, "too_many_topics")
    with _DESK_GUARD:
        _write_json("for-you-topics.json", {"topics": topics})
    return topics


def load_topics():
    raw = _read_json("for-you-topics.json").get("topics") or []
    if not isinstance(raw, list):
        return []
    try:
        return _normalize_many([str(t) for t in raw])
    except ForYouRejected:
        return []


def _token_record():
    return _read_json("x-user.json")


def _public_link(rec):
    if not rec:
        return "disconnected", "", [], False
    username = str(rec.get("username") or "")
    if rec.get("link") == "needs_reauth":
        return "needs_reauth", username, [], False
    if not rec.get("access_token") and not rec.get("refresh_token"):
        return "disconnected", "", [], False
    tag = rec.get("link") or "unprobed"
    if tag not in ("unprobed", "connected", "degraded"):
        tag = "unprobed"
    refused = [x for x in (rec.get("refused") or []) if x in ("likes", "following")]
    return tag, username, refused, bool(rec.get("unreachable"))


def _message(tag, refused, unreachable):
    if tag == "disconnected":
        return "X is not connected. Interests come from the topics you type."
    if tag == "needs_reauth":
        return "X needs you to connect again. Topics you type still work."
    if tag == "unprobed":
        return "X is connected. The next batch reads likes and following, once."
    if tag == "degraded":
        bits = []
        if "likes" in refused:
            bits.append("likes")
        if "following" in refused:
            bits.append("following")
        if unreachable and not bits:
            return "X could not be reached. Topics you type are included."
        if bits:
            return "X refused %s for this app's access tier. Topics you type are included." % " and ".join(bits)
        return "X is connected, with a limit on what this app can read. Topics you type are included."
    return "X is connected."


def link_tag():
    with _DESK_GUARD:
        tag, _username, _refused, _unreachable = _public_link(_token_record())
    return tag


def take_notice():
    global _NOTICE
    text, _NOTICE = _NOTICE, ""
    return text


def _set_notice(text):
    global _NOTICE
    _NOTICE = text


def public_status():
    cid, secret = _client()
    with _DESK_GUARD:
        tag, username, refused, unreachable = _public_link(_token_record())
        topics = load_topics()
    notice = take_notice()
    return {
        "connection": tag,
        "message": notice or _message(tag, refused, unreachable),
        "username": username,
        "topics": topics,
        "x_configured": bool(cid and secret),
        "grok": bool(os.environ.get("XAI_API_KEY", "").strip()),
        "batch_max": MAX_BATCH,
    }


def disconnect():
    with _DESK_GUARD:
        path = os.path.join(_desk(), "x-user.json")
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    return public_status()


def _loopback_host(host_header):
    from engine.server import LOCAL_HOSTS, split_host
    return split_host(host_header) in LOCAL_HOSTS


def _loopback_peer(peer):
    host = peer[0] if isinstance(peer, tuple) and peer else str(peer or "")
    host = host.lower().strip().strip("[]")
    if host.startswith("::ffff:"):
        host = host[7:]
    return host in {"127.0.0.1", "::1", "localhost"}


def redirect_uri(port):
    port = int(port)
    if port < 1 or port > 65535:
        raise ForYouRejected("The server port is not a redirect port.", "oauth_unconfigured")
    return "http://127.0.0.1:%d/api/for-you/x/callback" % port


def start_login(port):
    cid, secret = _client()
    if not cid or not secret:
        raise ForYouRejected(
            "X sign-in needs X_CLIENT_ID and X_CLIENT_SECRET in the environment.",
            "oauth_unconfigured")
    uri = redirect_uri(port)
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    with _PENDING_LOCK:
        now = time.monotonic()
        dead = [key for key, row in _PENDING.items() if row["exp"] <= now]
        for key in dead:
            _PENDING.pop(key, None)
        _PENDING[state] = {"verifier": verifier, "exp": now + PKCE_TTL_S, "redirect": uri}
    query = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": cid,
        "redirect_uri": uri,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return AUTH_URL + "?" + query


def finish_login(code, state, host_header, peer, error="", port=53343):
    """Exchange a code. A non-loopback peer or host is refused before the state is spent."""
    if not _loopback_peer(peer) or not _loopback_host(host_header):
        raise ForYouRejected("forbidden", "oauth_peer", 403)
    if error:
        _set_notice("X did not finish sign-in. Topics you type still work.")
        raise ForYouRejected("X did not finish sign-in.", "oauth_state")
    with _PENDING_LOCK:
        row = _PENDING.pop(state or "", None)
    if not row or row["exp"] <= time.monotonic():
        _set_notice("That sign-in link expired. Connect again.")
        raise ForYouRejected("That sign-in link expired. Connect again.", "oauth_state")
    if not code:
        _set_notice("X did not return a sign-in code. Connect again.")
        raise ForYouRejected("X did not return a sign-in code.", "oauth_state")
    cid, secret = _client()
    if not cid or not secret:
        _set_notice("X sign-in needs X_CLIENT_ID and X_CLIENT_SECRET in the environment.")
        raise ForYouRejected(
            "X sign-in needs X_CLIENT_ID and X_CLIENT_SECRET in the environment.",
            "oauth_unconfigured")
    basic = base64.b64encode(("%s:%s" % (cid, secret)).encode()).decode()
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": row["redirect"],
        "code_verifier": row["verifier"],
        "client_id": cid,
    })
    try:
        status, payload = _transport("POST", TOKEN_URL, {
            "Authorization": "Basic " + basic,
            "Content-Type": "application/x-www-form-urlencoded",
        }, body)
    except TransportError:
        _set_notice("X did not accept the sign-in. Connect again.")
        raise ForYouRejected("X did not accept the sign-in. Connect again.", "oauth_state")
    if status != 200 or not isinstance(payload, dict) or not payload.get("access_token"):
        _set_notice("X did not accept the sign-in. Connect again.")
        raise ForYouRejected("X did not accept the sign-in. Connect again.", "oauth_state")
    _store_token(payload, username="", user_id="", link="unprobed", refused=[], unreachable=False)
    _set_notice("X is connected. The next batch reads likes and following.")
    return None


def _store_token(payload, username, user_id, link, refused, unreachable):
    expires_in = payload.get("expires_in")
    try:
        ttl = int(expires_in)
    except (TypeError, ValueError):
        ttl = 7200
    rec = {
        "access_token": payload.get("access_token") or "",
        "refresh_token": payload.get("refresh_token") or "",
        "expires_at": time.time() + max(60, ttl),
        "username": username,
        "user_id": user_id,
        "link": link,
        "refused": list(refused),
        "unreachable": bool(unreachable),
    }
    with _DESK_GUARD:
        _write_json("x-user.json", rec)


def _refresh(rec):
    refresh = rec.get("refresh_token") or ""
    cid, secret = _client()
    if not refresh or not cid or not secret:
        return None
    basic = base64.b64encode(("%s:%s" % (cid, secret)).encode()).decode()
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": cid,
    })
    try:
        status, payload = _transport("POST", TOKEN_URL, {
            "Authorization": "Basic " + basic,
            "Content-Type": "application/x-www-form-urlencoded",
        }, body)
    except TransportError:
        return None
    if status != 200 or not isinstance(payload, dict) or not payload.get("access_token"):
        return None
    if not payload.get("refresh_token"):
        payload["refresh_token"] = refresh
    return payload


def _access_token():
    """Return a bearer token, or None when X is not usable. Updates the desk on refresh failure."""
    with _DESK_GUARD:
        rec = _token_record()
    if not rec.get("access_token") and not rec.get("refresh_token"):
        return None, "disconnected"
    if rec.get("expires_at") and float(rec.get("expires_at") or 0) < time.time() + 60:
        refreshed = _refresh(rec)
        if not refreshed:
            _mark_link(rec.get("username") or "", rec.get("user_id") or "", "needs_reauth", [], False)
            with _DESK_GUARD:
                current = _token_record()
                current.pop("access_token", None)
                current.pop("refresh_token", None)
                current["link"] = "needs_reauth"
                _write_json("x-user.json", current)
            return None, "needs_reauth"
        _store_token(refreshed, rec.get("username") or "", rec.get("user_id") or "",
                     rec.get("link") or "unprobed", rec.get("refused") or [], rec.get("unreachable"))
        rec = _token_record()
    token = rec.get("access_token") or ""
    if not token:
        return None, "needs_reauth"
    return token, rec.get("link") or "unprobed"


def _transport(method, url, headers, body):
    if TRANSPORT is not None:
        return TRANSPORT(method, url, headers, body)
    import ssl, urllib.error, urllib.request
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            raise TransportError(code)
    data = body.encode("utf-8") if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ctx))
    try:
        with opener.open(req, timeout=12) as resp:
            raw = resp.read(1_000_000)
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                payload = {}
            return resp.status, payload
    except TransportError:
        raise
    except urllib.error.HTTPError as exc:
        try:
            exc.read(2048)
        except Exception:
            pass
        raise TransportError(exc.code)
    except Exception as exc:
        raise TransportError(0) from exc


def _x_get(token, path):
    return _transport("GET", X_API + path, {"Authorization": "Bearer " + token}, None)


def _compile_signals(likes, following):
    accounts = []
    hashtags = {}
    keywords = {}
    liked_accounts = []
    users = {}
    includes = (likes or {}).get("includes") if isinstance(likes, dict) else {}
    for user in (includes or {}).get("users") or []:
        if isinstance(user, dict) and user.get("id") and user.get("username"):
            users[str(user["id"])] = str(user["username"])
    for tweet in (likes or {}).get("data") or []:
        if not isinstance(tweet, dict):
            continue
        ents = tweet.get("entities") or {}
        for tag in ents.get("hashtags") or []:
            name = str((tag or {}).get("tag") or "").strip().lstrip("#")
            if name:
                hashtags[name.lower()] = hashtags.get(name.lower(), 0) + 1
        for word in _tokens(tweet.get("text") or ""):
            keywords[word] = keywords.get(word, 0) + 1
        author = users.get(str(tweet.get("author_id") or ""))
        if author:
            liked_accounts.append(author)
    for user in (following or {}).get("data") or []:
        if isinstance(user, dict) and user.get("username"):
            accounts.append(str(user["username"]))
            for word in _tokens(user.get("description") or "")[:4]:
                keywords[word] = keywords.get(word, 0) + 1
    def top(pairs, n):
        return tuple(name for name, _n in sorted(pairs, key=lambda row: (-row[1], row[0]))[:n])
    return {
        "followed": tuple(dict.fromkeys(accounts))[:8],
        "liked_accounts": tuple(dict.fromkeys(liked_accounts))[:8],
        "hashtags": top(hashtags.items(), 8),
        "keywords": tuple(sorted(keywords.items(), key=lambda row: (-row[1], row[0]))[:12]),
        "likes_read": len((likes or {}).get("data") or []) if isinstance(likes, dict) else 0,
        "following_read": len((following or {}).get("data") or []) if isinstance(following, dict) else 0,
    }


def _mark_link(username, user_id, link, refused, unreachable):
    with _DESK_GUARD:
        rec = _token_record()
    if not rec.get("access_token"):
        return
    rec["username"] = username or rec.get("username") or ""
    rec["user_id"] = user_id or rec.get("user_id") or ""
    rec["link"] = link
    rec["refused"] = list(refused)
    rec["unreachable"] = bool(unreachable)
    with _DESK_GUARD:
        _write_json("x-user.json", rec)


def read_signals():
    """One page of likes and one page of following. 403 degrades that side and continues."""
    token, _tag = _access_token()
    if not token:
        tag, username, refused, unreachable = _public_link(_token_record())
        return None, tag, username, refused, unreachable
    refused = []
    unreachable = False
    username = ""
    user_id = ""
    try:
        status, me = _x_get(token, "/users/me?user.fields=username")
    except TransportError as exc:
        status, me = exc.status, {}
    if status == 401:
        _mark_link("", "", "needs_reauth", [], False)
        with _DESK_GUARD:
            rec = _token_record()
            rec.pop("access_token", None)
            _write_json("x-user.json", rec)
        return None, "needs_reauth", "", [], False
    if status == 200 and isinstance(me, dict):
        data = me.get("data") or {}
        username = str(data.get("username") or "")
        user_id = str(data.get("id") or "")
    elif status in (0, 403):
        unreachable = True
    likes = following = None
    if user_id:
        for name, path in (
            ("likes", "/users/%s/liked_tweets?max_results=100&tweet.fields=entities,text&expansions=author_id&user.fields=username" % user_id),
            ("following", "/users/%s/following?max_results=100&user.fields=username,name,description" % user_id),
        ):
            try:
                status, payload = _x_get(token, path)
            except TransportError as exc:
                status, payload = exc.status, {}
            if status == 401:
                _mark_link(username, user_id, "needs_reauth", [], False)
                return None, "needs_reauth", username, [], False
            if status == 403:
                refused.append(name)
                continue
            if status == 200 and isinstance(payload, dict):
                if name == "likes":
                    likes = payload
                else:
                    following = payload
            else:
                unreachable = True
    signals = _compile_signals(likes, following) if (likes or following) else None
    if refused or unreachable:
        link = "degraded"
    elif signals or user_id:
        link = "connected"
    else:
        link = "degraded"
        unreachable = True
    _mark_link(username, user_id, link, refused, unreachable)
    return signals, link, username, refused, unreachable


def _summary_text(typed, signals):
    lines = ["typed: " + ", ".join(typed)]
    if not signals:
        return "\n".join(lines)[:1200]
    lines.append("followed: " + ", ".join(signals["followed"]))
    lines.append("liked accounts: " + ", ".join(signals["liked_accounts"]))
    lines.append("hashtags: " + ", ".join(signals["hashtags"]))
    lines.append("keywords: " + ", ".join("%s:%d" % pair for pair in signals["keywords"]))
    return "\n".join(lines)[:1200]


def _signal_topics(signals):
    allowed = set()
    because = {}
    if not signals:
        return allowed, because
    for tag in signals["hashtags"]:
        allowed.add(_topic_key(tag))
        because[_topic_key(tag)] = "hashtag " + tag
    for word, _count in signals["keywords"]:
        allowed.add(_topic_key(word))
        because.setdefault(_topic_key(word), "from your likes")
    for name in signals["followed"]:
        allowed.add(_topic_key(name))
        because[_topic_key(name)] = "you follow @" + name
    for name in signals["liked_accounts"]:
        allowed.add(_topic_key(name))
        because.setdefault(_topic_key(name), "you liked @" + name)
    return allowed, because


def _heuristic_fill(typed, signals):
    have = {_topic_key(t) for t in typed}
    allowed, because = _signal_topics(signals)
    out = []
    for key in allowed:
        if key in have:
            continue
        topic = key
        out.append((topic, because.get(key, "from your likes")))
        have.add(key)
        if len(typed) + len(out) >= MAX_BATCH:
            break
    return out


def _grok_fill(typed, signals):
    allowed, because = _signal_topics(signals)
    if not allowed:
        return []
    text = _summary_text(typed, signals)
    if RANKER is not None:
        raw = RANKER(text)
    else:
        key = os.environ.get("XAI_API_KEY", "").strip()
        if not key:
            raise RuntimeError("no key")
        model = os.environ.get("XAI_MODEL", "").strip() or "grok-4-fast"
        prompt = (
            "Return JSON {\"interests\":[{\"topic\":\"...\",\"because\":\"...\"}]}. "
            "Use only the signals. Do not invent a topic that is not in the signals. "
            "Keep every typed topic out of the list. At most %d extra topics.\n%s"
            % (MAX_BATCH - len(typed), text)
        )
        status, payload = _transport("POST", GROK_URL, {
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        }, json.dumps({
            "model": model,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }))
        if status != 200 or not isinstance(payload, dict):
            raise RuntimeError("grok")
        raw = (((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    raw = str(raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    data = json.loads(raw)
    out = []
    have = {_topic_key(t) for t in typed}
    for row in (data.get("interests") or []) if isinstance(data, dict) else []:
        if not isinstance(row, dict):
            continue
        topic = str(row.get("topic") or "").strip()
        key = _topic_key(topic)
        if key not in allowed or key in have:
            continue
        from engine.topic_web import EmptyTopic, normalize_topic
        try:
            topic = normalize_topic(topic if topic else key)
        except EmptyTopic:
            topic = key
        why = str(row.get("because") or because.get(key) or "from your likes")[:160]
        out.append((topic, why))
        have.add(key)
        if len(typed) + len(out) >= MAX_BATCH:
            break
    return out


def rank(typed, signals):
    """Typed topics keep their slots. Signals fill what remains. Four typed topics skip the model."""
    if len(typed) >= MAX_BATCH or not signals:
        extra, note = [], None
    else:
        extra, note = _rank_extra(typed, signals)
    interests = [Interest(topic, "you typed this") for topic in typed]
    have = {_topic_key(topic) for topic in typed}
    for topic, why in extra:
        if _topic_key(topic) in have:
            continue
        interests.append(Interest(topic, why))
        have.add(_topic_key(topic))
        if len(interests) >= MAX_BATCH:
            break
    return interests[:MAX_BATCH], note


def _rank_extra(typed, signals):
    use_grok = RANKER is not None or bool(os.environ.get("XAI_API_KEY", "").strip())
    if not use_grok:
        return _heuristic_fill(typed, signals), "Ranked on this machine. Set XAI_API_KEY for a Grok ranking."
    try:
        return _grok_fill(typed, signals), None
    except Exception:
        return _heuristic_fill(typed, signals), "Ranked on this machine because Grok was unavailable."


def _stories():
    path = NEWS_PATH or os.path.join(ROOT, "dashboard", "x_news.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    rows = data.get("stories") if isinstance(data, dict) else None
    return [row for row in rows or [] if isinstance(row, dict) and row.get("title")]


def _story_blob(story):
    bits = [story.get("title") or "", story.get("hook") or "", story.get("summary") or ""]
    bits.extend(story.get("topics") or [])
    bits.extend(story.get("entities") or [])
    top = story.get("top_post") or {}
    if isinstance(top, dict) and top.get("username"):
        bits.append(top["username"])
    return " ".join(str(bit) for bit in bits)


def _discover(topic):
    if DISCOVER is not None:
        return DISCOVER(topic)
    from engine.topic_web import discover_web
    return discover_web(topic)


def match_interest(interest):
    tokens = _tokens(interest.topic)
    best = None
    best_score = 0
    for story in _stories():
        blob = set(_tokens(_story_blob(story)))
        score = sum(1 for token in tokens if token in blob)
        if score > best_score:
            best, best_score = story, score
    talk = interest.topic
    origin = ""
    title = ""
    url = ""
    broad = len(tokens) == 1 and tokens[0] in BROAD
    if best is not None and best_score > 0:
        title = str(best.get("title") or "")
        url = str(best.get("url") or "")
        origin = "x_news"
        if broad:
            talk = _safe_topic(title) or talk
    elif broad:
        try:
            found = _discover(interest.topic) or []
        except Exception:
            found = []
        if found:
            cand = found[0]
            title = str(getattr(cand, "title", "") or "")
            url = str(getattr(cand, "url", "") or "")
            origin = "web"
            talk = _safe_topic(title) or talk
    return {
        "topic": interest.topic,
        "because": interest.because,
        "talk_topic": talk,
        "story_title": title,
        "story_url": url,
        "story_origin": origin,
    }


def _safe_topic(title):
    from engine.topic_web import EmptyTopic, normalize_topic
    try:
        return normalize_topic(title)
    except EmptyTopic:
        return ""


def _admit(topics, summary, topics_given):
    if not isinstance(summary, bool):
        raise ForYouRejected("summary must be true or false.", "bad_summary")
    if topics is None and not topics_given:
        chosen = load_topics()
    else:
        if topics is not None and not isinstance(topics, list):
            raise ForYouRejected("topics must be a list of strings.", "bad_topic")
        chosen = _normalize_many(topics or [])
        if topics_given:
            save_topics(chosen)
    if len(chosen) > MAX_BATCH:
        raise ForYouRejected(
            "A batch holds %d topics. Remove one and try again." % MAX_BATCH, "too_many_topics")
    return chosen


class Batch:
    """One batch. Call run. The HTTP door is what writes the job poll."""

    def __init__(self, topics=None, summary=False, topics_given=False):
        self.summary = summary
        self.topics_given = bool(topics_given)
        self.topics = _admit(topics, summary, self.topics_given)
        self.fingerprint = "|".join(self.topics) + "|" + ("1" if summary else "0")

    def run(self, agent="anonymous", on_update=None, vocals_rejected=False):
        if vocals_rejected:
            raise ForYouRejected('The batch takes "summary": true or false. It does not take vocals.', "bad_vocals")
        started = time.perf_counter()
        interests, link, message, rank_note = self._plan()
        if not interests:
            raise ForYouRejected(
                "Add a topic, or connect X, before making a batch.", "empty_batch")
        items = [_item_shell(row, "queued") for row in interests]
        view = _view("running", "ranking", 10, link, message, rank_note, self.summary, items)
        _emit(on_update, view)
        made = []
        for index, row in enumerate(interests):
            items[index] = _item_shell(row, "matching")
            _emit(on_update, _view("running", "matching", _progress(index, len(interests), 0.3),
                                   link, message, rank_note, self.summary, items))
            rendered = match_interest(row)
            items[index] = {**_item_shell(row, "rendering"), **_story_fields(rendered)}
            _emit(on_update, _view("running", "rendering", _progress(index, len(interests), 0.6),
                                   link, message, rank_note, self.summary, items))

            def prog(stage, pct, i=index):
                width = _progress(i, len(interests), 0.6 + (float(pct or 0) / 100) * 0.4)
                _emit(on_update, _view("running", "rendering", width, link, message, rank_note, self.summary, items))

            try:
                result = _render(rendered["talk_topic"], "summary" if self.summary else "none", agent, prog)
            except Exception as exc:
                from engine.pipeline import TopicNotFound
                from engine.ingest import IngestError
                kind = "topic_not_found" if isinstance(exc, TopicNotFound) else None
                if isinstance(exc, IngestError):
                    kind = None
                items[index] = {
                    **items[index],
                    "state": "failed",
                    "error": str(exc)[:300],
                    "error_kind": kind,
                }
                _credit_item(agent, started, error=str(exc)[:200])
            else:
                track = (result or {}).get("track") or {}
                items[index] = {
                    **items[index],
                    "state": "done",
                    "track_url": track.get("url") or "",
                    "track_file": track.get("file") or "",
                    "title": track.get("title") or rendered["talk_topic"],
                    "error": None,
                    "error_kind": None,
                }
                made.append({
                    "topic": row.topic,
                    "title": items[index]["title"],
                    "track_url": items[index]["track_url"],
                    "track_file": items[index]["track_file"],
                })
                _credit_item(agent, started, result=result)
            _emit(on_update, _view("running", "rendering", _progress(index + 1, len(interests), 1),
                                   link, message, rank_note, self.summary, items))
        if made:
            _rebuild_catalog()
        final = _view("done", "done", 100, link, message, rank_note, self.summary, items)
        final["result"] = {"made": len(made), "failed": len(items) - len(made), "tracks": made}
        _emit(on_update, final)
        try:
            from engine import usage
            usage.record(agent or "anonymous", "for_you_batch", True, time.perf_counter() - started,
                         tracks=len(made))
        except Exception:
            pass
        return final

    def _plan(self):
        signals = None
        link, username, refused, unreachable = "disconnected", "", [], False
        full = len(self.topics) >= MAX_BATCH
        if not full:
            signals, link, username, refused, unreachable = read_signals()
            if signals is None and link == "disconnected":
                tag, username, refused, unreachable = _public_link(_token_record())
                link = tag
        else:
            tag, username, refused, unreachable = _public_link(_token_record())
            link = tag if tag != "disconnected" else "disconnected"
        interests, note = rank(self.topics, signals if not full else None)
        if not interests and signals:
            interests, note = rank([], signals)
        return interests, link, _message(link, refused, unreachable), note


def preview(topics=None, topics_given=False):
    """Rank and match without rendering audio."""
    batch = Batch(topics=topics, summary=False, topics_given=topics_given)
    interests, link, message, note = batch._plan()
    rows = [match_interest(row) for row in interests]
    return {
        "connection": link,
        "message": message,
        "rank_note": note,
        "interests": rows,
    }


def absolutize(view, base):
    base = (base or "").rstrip("/")
    out = json.loads(json.dumps(view))
    for item in out.get("items") or []:
        url = item.get("track_url") or ""
        if url.startswith("/"):
            item["track_url"] = base + url
    tracks = ((out.get("result") or {}).get("tracks") or [])
    for track in tracks:
        url = track.get("track_url") or ""
        if url.startswith("/"):
            track["track_url"] = base + url
    return out


def redact_requestline(line):
    text = str(line or "")
    if "/api/for-you/x/callback" not in text:
        return text
    parts = text.split(" ")
    if len(parts) < 2:
        return text
    parts[1] = parts[1].split("?", 1)[0]
    return " ".join(parts)


def _item_shell(interest, state):
    return {
        "topic": interest.topic,
        "because": interest.because,
        "talk_topic": interest.topic,
        "state": state,
        "story_title": "",
        "story_url": "",
        "story_origin": "",
        "track_url": "",
        "track_file": "",
        "title": interest.topic,
        "error": None,
        "error_kind": None,
    }


def _story_fields(rendered):
    return {
        "talk_topic": rendered["talk_topic"],
        "story_title": rendered["story_title"],
        "story_url": rendered["story_url"],
        "story_origin": rendered["story_origin"],
        "title": rendered["talk_topic"],
    }


def _view(state, stage, progress, link, message, rank_note, summary, items):
    return {
        "kind": "for_you",
        "state": state,
        "stage": stage,
        "progress": int(max(0, min(100, progress))),
        "connection": link,
        "message": message,
        "rank_note": rank_note,
        "summary": bool(summary),
        "error": None,
        "error_kind": None,
        "items": [dict(item) for item in items],
    }


def _progress(index, total, frac):
    if total <= 0:
        return 100
    return 15 + (80 * (index + frac) / total)


def _emit(on_update, view):
    if on_update:
        on_update(view)


def _render(topic, vocals, agent, progress):
    if RENDER is not None:
        return RENDER(topic, vocals, agent, progress)
    from engine.pipeline import synthesize_topic
    return synthesize_topic(topic, source="web", vocals=vocals, script=None, agent=agent,
                            progress=progress, report_status=False)


def _credit_item(agent, started, result=None, error=""):
    try:
        from engine import usage
        track = (result or {}).get("track") or {}
        timing = (result or {}).get("timing") or {}
        audio = float(track.get("duration_s") or 0) if result else 0.0
        cached = bool(timing.get("track_cached"))
        made = 0 if error or cached or not audio else 1
        usage.record(agent or "anonymous", "synthesize_topic", not error, max(0.0, time.perf_counter() - started),
                     audio_s=audio, tracks=made, error=(error or "")[:200])
    except Exception:
        pass


def _rebuild_catalog():
    import importlib.util
    path = os.path.join(ROOT, "dashboard", "botstatus.py")
    spec = importlib.util.spec_from_file_location("lofi_botstatus_for_you", path)
    if spec is None or spec.loader is None:
        return
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.rebuild_tracks()
