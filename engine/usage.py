"""Local usage log for the URL-to-Lofi engine and MCP server.

Events stay on disk under data/ (gitignored). Nothing here contacts an
analytics service or the network. All-time totals live in a small JSON file
so they survive the raw-log rotation. The raw JSONL is a capped debug log.
"""
import datetime, json, os, re, tempfile, threading
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_env_data = os.environ.get("LOFI_DATA_DIR")
DATA_DIR = os.path.abspath(_env_data) if _env_data else os.path.join(ROOT, "data")
LOG_PATH = os.path.join(DATA_DIR, "usage.jsonl")
TOTALS_PATH = os.path.join(DATA_DIR, "usage-totals.json")
TZ = ZoneInfo("America/Chicago")

MAX_BYTES = int(os.environ.get("LOFI_USAGE_MAX_BYTES") or 262144)
MAX_EVENTS = int(os.environ.get("LOFI_USAGE_MAX_EVENTS") or 2000)
# Local budget the dashboard compares X reads against. This process never calls X.
# Override with LOFI_X_READ_CAP. 100 is a free-tier-sized monthly ceiling, not a live lookup.
X_FREE_TIER_MONTHLY_READS = int(os.environ.get("LOFI_X_READ_CAP") or 100)

_LOCK = threading.Lock()
_AGENT_RE = re.compile(r"[^A-Za-z0-9._:-]")
_KEEP_DAYS = 45
_KEEP_HOURS = 48
_MAX_AGENTS = 64


def _now():
    return datetime.datetime.now(TZ)


def _zeros():
    return {"calls": 0, "errors": 0, "tracks": 0, "audio_s": 0.0, "x_reads": 0}


def _day_key(dt):
    return dt.date().isoformat()


def _hour_key(dt):
    return dt.strftime("%Y-%m-%dT%H")


def _month_key(dt):
    return dt.strftime("%Y-%m")


def caller(body, headers=None):
    """Agent id from a JSON body or X-Agent-Id. Missing or blank becomes 'anonymous'."""
    raw = ""
    if isinstance(body, dict):
        raw = body.get("agent_id") or body.get("agent") or ""
    if not str(raw).strip() and headers is not None:
        raw = headers.get("X-Agent-Id") or headers.get("x-agent-id") or ""
    raw = _AGENT_RE.sub("", str(raw).strip())[:64]
    return raw or "anonymous"


def _atomic_write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try: os.unlink(tmp)
        except FileNotFoundError: pass
        raise


def _object_buckets(value):
    if not isinstance(value, dict):
        return {}
    return {key: row for key, row in value.items() if isinstance(row, dict)}


def _int_map(value):
    if not isinstance(value, dict):
        return {}
    out = {}
    for key, row in value.items():
        if isinstance(row, bool) or not isinstance(row, (int, float)):
            continue
        out[key] = int(row)
    return out


def _load_totals():
    empty = {"all_time": _zeros(), "days": {}, "agents": {}, "hours": {}, "months_x": {}}
    try:
        with open(TOTALS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("all_time"), dict):
            return empty
        data["days"] = _object_buckets(data.get("days"))
        data["agents"] = _object_buckets(data.get("agents"))
        data["tools"] = _object_buckets(data.get("tools"))
        data["hours"] = _int_map(data.get("hours"))
        data["months_x"] = _int_map(data.get("months_x"))
        return data
    except Exception:
        return empty


def _count(value):
    """A stored counter, or 0 when the file holds a non-number."""
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _seconds(value):
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _add(bucket, *, ok, tracks, audio_s, x_reads):
    bucket["calls"] = _count(bucket.get("calls")) + 1
    if not ok:
        bucket["errors"] = _count(bucket.get("errors")) + 1
    bucket["tracks"] = _count(bucket.get("tracks")) + int(tracks or 0)
    bucket["audio_s"] = round(_seconds(bucket.get("audio_s")) + float(audio_s or 0), 3)
    bucket["x_reads"] = _count(bucket.get("x_reads")) + int(x_reads or 0)


def _prune(totals, now):
    days = totals.setdefault("days", {})
    cutoff_d = (now.date() - datetime.timedelta(days=_KEEP_DAYS)).isoformat()
    for k in [k for k in days if k < cutoff_d]:
        days.pop(k, None)
    hours = totals.setdefault("hours", {})
    cutoff_h = (now - datetime.timedelta(hours=_KEEP_HOURS)).strftime("%Y-%m-%dT%H")
    for k in [k for k in hours if k < cutoff_h]:
        hours.pop(k, None)
    months = totals.setdefault("months_x", {})
    cutoff_m = (now.date() - datetime.timedelta(days=400)).strftime("%Y-%m")
    for k in [k for k in months if k < cutoff_m]:
        months.pop(k, None)
    agents = totals.setdefault("agents", {})
    if len(agents) > _MAX_AGENTS:
        ranked = sorted(agents, key=lambda a: _count((agents[a] or {}).get("calls") if isinstance(agents[a], dict) else 0))
        for a in ranked[:len(agents) - _MAX_AGENTS]:
            agents.pop(a, None)


def _rotate_log():
    try:
        size = os.path.getsize(LOG_PATH)
    except OSError:
        return
    if size <= MAX_BYTES:
        return
    try:
        with open(LOG_PATH, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return
    if len(lines) > MAX_EVENTS or size > MAX_BYTES:
        keep = lines[-max(1, MAX_EVENTS // 2):]
        # also honor the byte cap
        while keep and sum(len(x.encode()) for x in keep) > MAX_BYTES // 2:
            keep = keep[len(keep)//2 or 1:]
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".jsonl", dir=DATA_DIR)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.writelines(keep)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, LOG_PATH)
        except BaseException:
            try: os.unlink(tmp)
            except FileNotFoundError: pass
            raise


def record(agent, tool, ok, duration_s, audio_s=0.0, tracks=0, x_reads=0, error=""):
    """Append one event. Never raises into the caller."""
    try:
        who = _AGENT_RE.sub("", str(agent or "").strip())[:64] or "anonymous"
        tool = _AGENT_RE.sub("", str(tool or "unknown").strip())[:40] or "unknown"
        now = _now()
        event = {
            "ts": now.isoformat(timespec="seconds"),
            "agent": who,
            "tool": tool,
            "ok": bool(ok),
            "duration_s": round(float(duration_s or 0), 3),
            "audio_s": round(float(audio_s or 0), 3),
            "tracks": int(tracks or 0),
            "x_reads": int(x_reads or 0),
        }
        if error:
            event["error"] = str(error)[:200]
        line = json.dumps(event, ensure_ascii=False) + "\n"
        with _LOCK:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
            _rotate_log()
            totals = _load_totals()
            _add(totals.setdefault("all_time", _zeros()), ok=ok, tracks=tracks, audio_s=audio_s, x_reads=x_reads)
            day = totals.setdefault("days", {}).setdefault(_day_key(now), _zeros())
            # day buckets also keep a per-agent split under "by"
            _add(day, ok=ok, tracks=tracks, audio_s=audio_s, x_reads=x_reads)
            if not isinstance(day.get("by"), dict):
                day["by"] = {}
            by = day.setdefault("by", {})
            _add(by.setdefault(who, _zeros()), ok=ok, tracks=tracks, audio_s=audio_s, x_reads=x_reads)
            _add(totals.setdefault("agents", {}).setdefault(who, _zeros()), ok=ok, tracks=tracks, audio_s=audio_s, x_reads=x_reads)
            _add(totals.setdefault("tools", {}).setdefault(tool, _zeros()), ok=ok, tracks=tracks, audio_s=audio_s, x_reads=x_reads)
            hk = _hour_key(now)
            totals.setdefault("hours", {})[hk] = int(totals["hours"].get(hk) or 0) + 1
            if x_reads:
                mk = _month_key(now)
                totals.setdefault("months_x", {})[mk] = int(totals["months_x"].get(mk) or 0) + int(x_reads)
            _prune(totals, now)
            _atomic_write(TOTALS_PATH, totals)
    except Exception:
        return


def _public_bucket(b):
    if not isinstance(b, dict):
        b = {}
    calls = _count(b.get("calls"))
    errors = _count(b.get("errors"))
    audio = _seconds(b.get("audio_s"))
    return {
        "calls": calls,
        "errors": errors,
        "error_rate": round(errors / calls, 3) if calls else 0.0,
        "tracks": _count(b.get("tracks")),
        "audio_s": round(audio, 3),
        "audio_min": round(audio / 60.0, 2),
        "x_reads": _count(b.get("x_reads")),
    }


def _agent_rows(bucket):
    if not isinstance(bucket, dict):
        return []
    rows = []
    for name, row in bucket.items():
        if not isinstance(row, dict):
            continue
        public = _public_bucket(row)
        public["agent"] = name
        rows.append(public)
    rows.sort(key=lambda r: (-r["calls"], r["agent"]))
    return rows


def note_x_search(agent, reads, ok=True, duration_s=0.0, error=""):
    """Record X API reads from a live search. This module never calls X itself."""
    record(agent or "anonymous", "x_search", ok, duration_s, x_reads=int(reads or 0) if ok else 0, error=error)


def summary():
    now = _now()
    with _LOCK:
        totals = _load_totals()
    today = (totals.get("days") or {}).get(_day_key(now)) or {}
    if not isinstance(today, dict):
        today = {}
    today_agents = _agent_rows(today.get("by"))
    all_agents = _agent_rows(totals.get("agents"))
    slots = []
    for back in range(23, -1, -1):
        dt = now - datetime.timedelta(hours=back)
        key = _hour_key(dt)
        slots.append((key, int((totals.get("hours") or {}).get(key) or 0), dt.strftime("%-I%p").lower()))
    month = _month_key(now)
    x_month = int((totals.get("months_x") or {}).get(month) or 0)
    cap = X_FREE_TIER_MONTHLY_READS
    raw_tools = totals.get("tools") if isinstance(totals.get("tools"), dict) else {}
    tools = {name: _public_bucket(row) for name, row in raw_tools.items() if isinstance(row, dict)}
    return {
        "generated": now.isoformat(timespec="seconds"),
        "timezone": "America/Chicago",
        "tools": tools,
        "x_free_tier_monthly_reads": cap,
        "x_reads_month": x_month,
        "x_reads_remaining": max(0, cap - x_month) if cap else 0,
        "today": _public_bucket(today),
        "all_time": _public_bucket(totals.get("all_time")),
        "agents_today": today_agents[:12],
        "agents": all_agents[:12],
        "hours": [n for _, n, _ in slots],
        "hour_labels": [lab for _, _, lab in slots],
    }
