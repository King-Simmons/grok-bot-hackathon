"""HTTP door for For You. This is the only writer of a For You job record."""
import time, urllib.parse, uuid

from engine import for_you, usage


def route_for_you_get(handler, parsed):
    path = parsed.path
    if path == "/api/for-you":
        handler._json(200, for_you.public_status())
        return True
    if path == "/api/for-you/connect":
        try:
            url = for_you.start_login(handler.server.server_address[1])
        except for_you.ForYouRejected as exc:
            handler._json(exc.http_status, {"error": str(exc), "error_kind": exc.error_kind})
            return True
        _redirect(handler, url)
        return True
    if path == "/api/for-you/x/callback":
        qs = urllib.parse.parse_qs(parsed.query)
        code = (qs.get("code") or [""])[0]
        state = (qs.get("state") or [""])[0]
        error = (qs.get("error") or [""])[0]
        try:
            for_you.finish_login(code, state, handler.headers.get("Host"), handler.client_address,
                                 error=error, port=handler.server.server_address[1])
        except for_you.ForYouRejected as exc:
            if exc.error_kind == "oauth_peer":
                handler._json(403, {"error": "forbidden"})
                return True
        _redirect(handler, "/dashboard/#for-you")
        return True
    return False


def route_for_you_post(handler, path):
    from engine.server import read_json_object
    if path == "/api/for-you/disconnect":
        _body, failed = read_json_object(handler, 1000)
        if failed:
            return True
        handler._json(200, for_you.disconnect())
        return True
    if path == "/api/for-you/topics":
        body, failed = read_json_object(handler, 8000)
        if failed:
            return True
        try:
            topics = for_you.save_topics(body.get("topics") or [])
        except for_you.ForYouRejected as exc:
            handler._json(exc.http_status, {"error": str(exc), "error_kind": exc.error_kind})
            return True
        handler._json(200, {"topics": topics})
        return True
    if path == "/api/for-you/preview":
        body, failed = read_json_object(handler, 8000)
        if failed:
            return True
        try:
            view = for_you.preview(body.get("topics"), topics_given="topics" in body)
        except for_you.ForYouRejected as exc:
            handler._json(exc.http_status, {"error": str(exc), "error_kind": exc.error_kind})
            return True
        handler._json(200, view)
        return True
    if path == "/api/for-you/batch":
        return _batch(handler)
    return False


def _batch(handler):
    from engine.server import JOBS, JOBS_LOCK, _reject_busy, _spawn_job, read_json_object
    body, failed = read_json_object(handler, 8000)
    if failed:
        return True
    who = usage.caller(body, handler.headers)
    if "vocals" in body:
        usage.record(who, "for_you_batch", False, 0, error="bad_vocals")
        handler._json(400, {"error": 'The batch takes "summary": true or false. It does not take vocals.',
                            "error_kind": "bad_vocals"})
        return True
    summary = body.get("summary", False)
    try:
        batch = for_you.Batch(topics=body.get("topics"), summary=summary, topics_given="topics" in body)
    except for_you.ForYouRejected as exc:
        usage.record(who, "for_you_batch", False, 0, error=exc.error_kind)
        handler._json(exc.http_status, {"error": str(exc), "error_kind": exc.error_kind})
        return True
    if not batch.topics and for_you.link_tag() == "disconnected":
        usage.record(who, "for_you_batch", False, 0, error="empty_batch")
        handler._json(400, {"error": "Add a topic, or connect X, before making a batch.",
                            "error_kind": "empty_batch"})
        return True
    with JOBS_LOCK:
        for jid, job in JOBS.items():
            if job.get("kind") == "for_you" and job.get("state") == "running" and job.get("fingerprint") == batch.fingerprint:
                handler._json(202, {"job_id": jid, "poll": "/api/jobs/" + jid, "joined": True})
                return True
    jid = uuid.uuid4().hex[:12]
    record = {
        "id": jid, "kind": "for_you", "fingerprint": batch.fingerprint, "url": "for you",
        "state": "running", "stage": "queued", "progress": 0, "started": time.time(),
        "summary": batch.summary, "items": [],
    }
    if not _spawn_job(jid, record, _run_batch, (jid, batch, who)):
        _reject_busy(handler, who, "for_you_batch")
        return True
    handler._json(202, {"job_id": jid, "poll": "/api/jobs/" + jid, "joined": False})
    return True


def _run_batch(jid, batch, agent):
    from engine.server import JOBS, JOBS_LOCK

    def publish(view):
        with JOBS_LOCK:
            job = JOBS.get(jid)
            if job is not None:
                job.update(view)
                job["state"] = "running"
                job["fingerprint"] = batch.fingerprint
                job["kind"] = "for_you"

    view = batch.run(agent=agent, on_update=publish)
    view["fingerprint"] = batch.fingerprint
    view["kind"] = "for_you"
    view["id"] = jid
    return view


def _redirect(handler, location):
    handler.send_response(302)
    handler.send_header("Location", location)
    handler.send_header("Content-Length", "0")
    handler.end_headers()
