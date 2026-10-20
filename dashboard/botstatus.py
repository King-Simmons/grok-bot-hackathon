#!/usr/bin/env python3
"""botstatus.py - tiny status protocol CLI for the Lofi News Radio control room.

One JSON file per bot/job in <repo>/status/<id>.json. Every set/clear also rebuilds
status/index.json (what the dashboard reads) and tracks.json (beat list).

This file lives in dashboard/ next to index.html. Status files live at the repo
root (status/), which dashboard/status symlinks to, so the page's relative
fetch of status/index.json works when you serve the repo root. tracks.json is
written next to this script. Beat paths in it are relative to the page
(../samples/beat-*.mp3 and ../tracks/*.mp3).

Stdlib only. See docs/STATUS_PROTOCOL.md.
"""
import argparse, datetime, glob, json, os, re, sys, tempfile
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.abspath(__file__))  # dashboard/ (page-relative outputs)
REPO = os.path.dirname(ROOT)
STATUS_DIR = os.path.join(REPO, "status")
INDEX = os.path.join(STATUS_DIR, "index.json")
TRACKS = os.path.join(ROOT, "tracks.json")
TZ = ZoneInfo("America/Chicago")
STATES = ("running", "idle", "done", "error")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
PROTOCOL_VERSION = 1


def now_iso():
    return datetime.datetime.now(TZ).isoformat(timespec="seconds")


def atomic_write_json(path, data):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)  # atomic on POSIX
    except BaseException:
        try: os.unlink(tmp)
        except FileNotFoundError: pass
        raise


def status_path(bot_id):
    if not ID_RE.match(bot_id):
        sys.exit(f"invalid --id {bot_id!r}: use lowercase letters, digits, '.', '_' or '-' (max 64)")
    if bot_id == "index":
        sys.exit("'index' is reserved")
    return os.path.join(STATUS_DIR, bot_id + ".json")


def read_all():
    bots, errors = [], []
    for p in sorted(glob.glob(os.path.join(STATUS_DIR, "*.json"))):
        name = os.path.basename(p)
        if name == "index.json" or name.startswith("."):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                b = json.load(f)
            if not isinstance(b, dict) or "id" not in b:
                raise ValueError("missing id")
            bots.append(b)
        except Exception as e:
            errors.append({"file": name, "error": str(e)})
    return bots, errors


def rebuild_index():
    bots, errors = read_all()
    order = {s: i for i, s in enumerate(("running", "error", "idle", "done"))}
    bots.sort(key=lambda b: (order.get(b.get("state"), 9), str(b.get("name", b["id"])).lower()))
    atomic_write_json(INDEX, {"protocol": PROTOCOL_VERSION, "generated": now_iso(),
                              "count": len(bots), "bots": bots, "invalid": errors})
    return bots


SIDECAR_KEYS = ("title", "label", "bpm", "key", "duration_s", "notes", "source_url", "mood_tags", "instruments", "created")


def _track_entry(p):
    st = os.stat(p)
    rel = os.path.relpath(p, ROOT).replace(os.sep, "/")
    t = {"file": rel, "title": os.path.basename(p)[:-4], "bytes": st.st_size,
         "modified": datetime.datetime.fromtimestamp(st.st_mtime, TZ).isoformat(timespec="seconds")}
    side = p[:-4] + ".json"  # optional sidecar metadata, e.g. beat-002.json {"title": ...}
    if os.path.exists(side):
        try:
            with open(side, encoding="utf-8") as f:
                meta = json.load(f)
            if isinstance(meta, dict):
                t.update({k: v for k, v in meta.items() if k in SIDECAR_KEYS})
        except Exception:
            pass
    return t


def _unique_paths(patterns):
    found, seen = [], set()
    for pattern in patterns:
        for p in glob.glob(pattern):
            rp = os.path.realpath(p)
            if rp in seen:
                continue
            seen.add(rp)
            found.append(p)
    return found


def rebuild_tracks():
    """Demo beats in samples/ (and any beat-*.mp3 next to the page) plus tracks/*.mp3
    from the URL engine at the repo root. Newest generated tracks first.
    File paths are relative to dashboard/ so the page can load them."""
    beats = [_track_entry(p) for p in sorted(_unique_paths([
        os.path.join(REPO, "samples", "beat-*.mp3"),
        os.path.join(ROOT, "beat-*.mp3"),
    ]))]
    gen = [_track_entry(p) for p in _unique_paths([
        os.path.join(REPO, "tracks", "*.mp3"),
        os.path.join(ROOT, "tracks", "*.mp3"),
    ])]
    gen.sort(key=lambda t: t["modified"], reverse=True)
    tracks = beats + gen
    atomic_write_json(TRACKS, {"generated": now_iso(), "count": len(tracks), "tracks": tracks})
    return tracks


def refresh():
    rebuild_index()
    rebuild_tracks()


def cmd_set(a):
    path = status_path(a.id)
    cur = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cur = json.load(f)
        except Exception:
            cur = {}
    rec = dict(cur)
    rec["id"] = a.id
    for k in ("name", "owner", "role", "state", "task", "artifact", "error"):
        v = getattr(a, k)
        if v is not None:
            rec[k] = v
    if a.progress is not None:
        if not 0 <= a.progress <= 100:
            sys.exit("--progress must be 0-100")
        rec["progress"] = a.progress
    for k in ("progress", "artifact", "error"):
        if getattr(a, "no_" + k):
            rec.pop(k, None)
    if rec.get("state") not in STATES:
        sys.exit(f"--state is required (one of {', '.join(STATES)})")
    if rec.get("state") != "error" and a.error is None and not a.keep_error:
        rec.pop("error", None)
    rec.setdefault("name", a.id)
    for req in ("owner", "task"):
        if not rec.get(req):
            sys.exit(f"--{req} is required for a new bot")
    rec["last_update"] = now_iso()
    atomic_write_json(path, rec)
    refresh()
    print(json.dumps(rec, indent=2, ensure_ascii=False))


def cmd_clear(a):
    if a.all:
        for p in glob.glob(os.path.join(STATUS_DIR, "*.json")):
            if os.path.basename(p) != "index.json":
                os.unlink(p)
    else:
        if not a.id:
            sys.exit("give --id or --all")
        p = status_path(a.id)
        if os.path.exists(p):
            os.unlink(p)
        else:
            print(f"no status file for {a.id}", file=sys.stderr)
    refresh()
    print("ok")


def cmd_list(a):
    bots = rebuild_index()
    if a.json:
        print(json.dumps(bots, indent=2, ensure_ascii=False)); return
    if not bots:
        print("(no bots reporting)"); return
    now = datetime.datetime.now(TZ)
    for b in bots:
        try:
            age = (now - datetime.datetime.fromisoformat(b["last_update"])).total_seconds() / 60
            ages = f"{age:.0f}m ago" + (" STALE" if age > 30 and b.get("state") in ("running", "idle") else "")
        except Exception:
            ages = "?"
        prog = f" {b['progress']}%" if "progress" in b else ""
        print(f"[{b.get('state','?'):7}] {b['id']:<16} {b.get('name','')} ({b.get('owner','?')}){prog} - {b.get('task','')}  [{ages}]")


def main():
    ap = argparse.ArgumentParser(description="Lofi News Radio bot status CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set", help="create or update a bot/job status (fields merge with existing)")
    s.add_argument("--id", required=True)
    s.add_argument("--name"); s.add_argument("--owner"); s.add_argument("--role")
    s.add_argument("--state", choices=STATES); s.add_argument("--task")
    s.add_argument("--progress", type=int); s.add_argument("--artifact"); s.add_argument("--error")
    s.add_argument("--no-progress", action="store_true", help="remove progress")
    s.add_argument("--no-artifact", action="store_true", help="remove artifact")
    s.add_argument("--no-error", action="store_true", help="remove error")
    s.add_argument("--keep-error", action="store_true", help="keep old error even if state is not error")
    s.set_defaults(fn=cmd_set)
    c = sub.add_parser("clear", help="delete a bot/job status file")
    c.add_argument("--id"); c.add_argument("--all", action="store_true")
    c.set_defaults(fn=cmd_clear)
    l = sub.add_parser("list", help="print all statuses (also rebuilds index.json)")
    l.add_argument("--json", action="store_true"); l.set_defaults(fn=cmd_list)
    r = sub.add_parser("refresh", help="rebuild status/index.json and tracks.json only")
    r.set_defaults(fn=lambda a: (refresh(), print("ok")))
    a = ap.parse_args(); a.fn(a)


if __name__ == "__main__":
    main()
