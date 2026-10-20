"""Live agent registry. Same files as dashboard/botstatus.py (status/<id>.json).

First contact creates the file. Later posts merge. running/idle agents with no
update for STALE_AFTER_S seconds are reported offline; the file is kept.
"""
import datetime, importlib.util, json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# dashboard/ is not a package. Load botstatus.py by path so the status files stay shared with the CLI.
_spec = importlib.util.spec_from_file_location("botstatus", os.path.join(ROOT, "dashboard", "botstatus.py"))
botstatus = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(botstatus)

MAX_AGENTS = 48
LIMITS = {"name": 80, "owner": 64, "role": 80, "task": 200, "artifact": 240, "error": 200}


class AgentError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _clean(value, limit):
    text = " ".join(str(value).split())
    return text[:limit]


def _skip(rec):
    ident = str(rec.get("id") or "")
    return bool(rec.get("example")) or ident.startswith("example-")


def _age_s(rec, now):
    try:
        then = datetime.datetime.fromisoformat(rec["last_update"])
        if then.tzinfo is None:
            then = then.replace(tzinfo=botstatus.TZ)
        return (now - then).total_seconds()
    except Exception:
        return None


def presence(rec, now=None):
    """online, offline, or settled (done/error never go offline)."""
    state = rec.get("state")
    if state not in ("running", "idle"):
        return "settled"
    age = _age_s(rec, now or datetime.datetime.now(botstatus.TZ))
    if age is None or age > botstatus.STALE_AFTER_S:
        return "offline"
    return "online"


def _count_live_files():
    n = 0
    if not os.path.isdir(botstatus.STATUS_DIR):
        return 0
    for name in os.listdir(botstatus.STATUS_DIR):
        if not name.endswith(".json") or name == "index.json" or name.startswith("."):
            continue
        if name.startswith("example-"):
            continue
        n += 1
    return n


def report(body):
    """Create or merge one status file. Raises AgentError on bad input."""
    if not isinstance(body, dict):
        raise AgentError("body must be a JSON object")
    ident = str(body.get("id") or "").strip()
    try:
        path = botstatus.status_path(ident)
    except ValueError as e:
        raise AgentError(str(e))
    exists = os.path.exists(path)
    if not exists and _count_live_files() >= MAX_AGENTS:
        raise AgentError(f"agent cap reached ({MAX_AGENTS})", status=409)
    cur = {}
    if exists:
        try:
            with open(path, encoding="utf-8") as f:
                cur = json.load(f)
            if not isinstance(cur, dict):
                cur = {}
        except Exception:
            cur = {}
    rec = dict(cur)
    rec["id"] = ident
    for key, limit in LIMITS.items():
        if key in body and body[key] is not None:
            rec[key] = _clean(body[key], limit)
    if "state" in body and body["state"] is not None:
        state = str(body["state"]).strip()
        if state not in botstatus.STATES:
            raise AgentError(f"state must be one of {', '.join(botstatus.STATES)}")
        rec["state"] = state
    if "progress" in body and body["progress"] is not None:
        try:
            progress = int(body["progress"])
        except (TypeError, ValueError):
            raise AgentError("progress must be an integer 0-100")
        if not 0 <= progress <= 100:
            raise AgentError("progress must be an integer 0-100")
        rec["progress"] = progress
    if rec.get("state") not in botstatus.STATES:
        raise AgentError(f"state is required (one of {', '.join(botstatus.STATES)})")
    rec.setdefault("name", ident)
    if not rec.get("owner"):
        raise AgentError("owner is required for a new agent")
    if not rec.get("task"):
        raise AgentError("task is required for a new agent")
    if rec.get("state") != "error":
        rec.pop("error", None)
    rec["last_update"] = botstatus.now_iso()
    botstatus.atomic_write_json(path, rec)
    botstatus.refresh()
    out = dict(rec)
    out["presence"] = presence(out)
    out["age_s"] = 0
    return out


def list_agents():
    now = datetime.datetime.now(botstatus.TZ)
    bots, errors = botstatus.read_all()
    live = []
    for rec in bots:
        if _skip(rec):
            continue
        item = dict(rec)
        item["presence"] = presence(item, now)
        age = _age_s(item, now)
        item["age_s"] = round(age, 1) if age is not None else None
        live.append(item)
    order = {"online": 0, "offline": 1, "settled": 2}
    state_order = {"running": 0, "error": 1, "idle": 2, "done": 3}
    live.sort(key=lambda b: (order.get(b["presence"], 9), state_order.get(b.get("state"), 9), str(b.get("name") or b["id"]).lower()))
    return {
        "protocol": botstatus.PROTOCOL_VERSION,
        "generated": botstatus.now_iso(),
        "stale_after_s": botstatus.STALE_AFTER_S,
        "count": len(live),
        "bots": live,
        "invalid": errors,
    }
