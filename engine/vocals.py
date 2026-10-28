"""Optional spoken vocals. Off unless the caller asks for summary or song.

The default synthesize path never imports this module. If Chatterbox is missing,
or a render fails, apply() returns the instrumental result unchanged.
"""
import hashlib, json, os, re, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

ATEMPO = 0.9
TEMPERATURE = 0.7
SEED = 7
SOFT_CHARS = 700
CALM = {"calm", "analytical", "dark", "melancholic"}
HOT = {"energetic", "upbeat", "bouncy"}
_API = None


def voice_api():
    """Shared loader in scripts/chatterbox_voice.py. Tests may set engine.vocals._API."""
    global _API
    if _API is not None:
        return _API
    scripts = os.path.join(ROOT, "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import chatterbox_voice
    _API = chatterbox_voice
    return _API


def wants(vocals):
    return str(vocals or "none").strip().lower() in ("summary", "song")


def preset_for(mood_tags, mode):
    primary = str((mood_tags or ["calm"])[0]).lower()
    if (mode == "song" and primary not in CALM) or primary in HOT:
        name, exaggeration, cfg = "energetic", 0.7, 0.3
    else:
        name, exaggeration, cfg = "dj", 0.5, 0.5
    return {"name": name, "exaggeration": exaggeration, "cfg_weight": cfg, "temperature": TEMPERATURE}


def plan_starts(bpm, durations, intro_bars=1, bars_per_line=2, beats_per_bar=4):
    """One bar of intro, then one line per two bars. A longer line pushes the next start to the following beat."""
    import math
    beat = 60.0 / float(bpm)
    bar = beat * beats_per_bar
    slot = bars_per_line * bar
    cursor = intro_bars * bar
    starts = []
    for dur in durations:
        starts.append(cursor)
        if float(dur) <= slot + 1e-9:
            cursor += slot
        else:
            cursor += beat * max(1, math.ceil((float(dur) - 1e-9) / beat))
    return starts


def _clean(text, limit):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    text = re.sub(r"https?://\S+", "", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].strip() or text[:limit].strip()
    return cut.rstrip(".,;:…")


def _sentence(text):
    text = _clean(text, 160)
    if not text:
        return ""
    return text if re.search(r"[.!?]$", text) else text + "."


def _topics(analysis, n=3):
    out = []
    for raw in (analysis or {}).get("topics") or []:
        word = re.sub(r"[^A-Za-z0-9+#-]", "", str(raw))
        if len(word) >= 3 and word.lower() not in {item.lower() for item in out}:
            out.append(word)
        if len(out) >= n:
            break
    return out or ["night"]


def summary_lines(mood, page, analysis):
    attrs = mood.get("musical_attributes") or {}
    title = _clean((page or {}).get("title") or mood.get("url") or "this page", 72)
    topics = _topics(analysis, 3)
    mood_word = str((attrs.get("mood_tags") or ["calm"])[0]).capitalize()
    lines = [
        "You're listening to Lofi News Radio.",
        _sentence(f"This beat is for {title}"),
        _sentence("On the table tonight: " + ", ".join(topics)),
        f"{mood_word} mood, {attrs.get('bpm', 72)} BPM, {attrs.get('key', 'A minor')}.",
    ]
    return [line for line in lines if line][:4]


def song_lines(mood, analysis):
    attrs = mood.get("musical_attributes") or {}
    topics = _topics(analysis, 2)
    return [
        _sentence(f"Late night static, {topics[0]} on the line"),
        _sentence(f"Keep it low, keep {topics[-1]} in time"),
        _sentence(f"{attrs.get('bpm', 72)} on the clock, {attrs.get('key', 'A minor')} in the room"),
        "Ride the beat, then leave it on the moon.",
    ]


def build_lines(mode, mood, page, analysis, script):
    if script is not None and str(script).strip():
        text = str(script).strip()
        if len(text) > SOFT_CHARS:
            text = text[:SOFT_CHARS].rsplit(" ", 1)[0].strip() or text[:SOFT_CHARS].strip()
        lines = []
        for part in re.split(r"\n+|(?<=[.!?])\s+", text):
            line = _sentence(part)
            if line:
                lines.append(line)
            if len(lines) >= 8:
                break
        if lines:
            return lines, "caller"
    if mode == "song":
        return song_lines(mood, analysis), "generated"
    return summary_lines(mood, page, analysis), "generated"


def _fallback(result, mode, reason):
    """Instrumental result plus why vocals did not render. The beat itself is unchanged."""
    out = dict(result)
    out["vocals"] = {
        "state": "instrumental",
        "mode": mode,
        "reason": str(reason or "Vocals were skipped. The instrumental still plays.")[:500],
    }
    return out


def apply(result, vocals, script, agent, name):
    """Add vocals. vocals=none returns the same result. A missing engine keeps the beat and says why."""
    if not wants(vocals):
        return result
    mode = str(vocals).strip().lower()
    try:
        api = voice_api()
        if not api.available():
            reason = getattr(api, "INSTALL", None) or (
                "Chatterbox is not installed, so vocals stay off and the instrumental still plays.")
            return _fallback(result, mode, reason)
        extra = render_optional(result, mode, script, agent, name)
    except Exception as exc:
        print(f"vocals skipped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return _fallback(result, mode, f"Vocals failed ({type(exc).__name__}). The instrumental still plays.")
    if not extra:
        return _fallback(result, mode, "Vocals were skipped. The instrumental still plays.")
    out = dict(result)
    out["vocals"] = extra["vocals"]
    if extra.get("track"):
        out["track"] = extra["track"]
    return out


def render_optional(result, mode, script, agent, name):
    api = voice_api()
    if not api.available():
        return None
    mood = result["mood"]
    page = result.get("page") or {}
    analysis = result.get("analysis") or {}
    beat_path = result["track"]["path"]
    beat_url = result["track"]["url"]
    lines, source = build_lines(mode, mood, page, analysis, script)
    attrs = mood.get("musical_attributes") or {}
    preset = preset_for(attrs.get("mood_tags"), mode)
    bpm = int(attrs.get("bpm") or 72)
    cache = os.path.join(ROOT, "tracks", "vocal-cache")
    os.makedirs(cache, exist_ok=True)
    started = time.perf_counter()
    rendered = []
    device = None
    first_line_s = None
    for index, text in enumerate(lines):
        seed = SEED + index
        treated = os.path.join(cache, _line_key(text, preset, seed) + ".wav")
        if not os.path.exists(treated):
            audio, sr, info = api.synthesize_line(
                text,
                exaggeration=preset["exaggeration"],
                cfg_weight=preset["cfg_weight"],
                temperature=preset["temperature"],
                seed=seed,
            )
            device = info.get("device") or device
            raw = treated + ".raw.wav"
            _write_wav(raw, audio, sr)
            _treat(raw, treated, seed)
            try:
                os.remove(raw)
            except OSError:
                pass
        if first_line_s is None:
            first_line_s = round(time.perf_counter() - started, 2)
        rendered.append({"text": text, "path": treated, "dur_s": _wav_seconds(treated)})
    starts = plan_starts(bpm, [row["dur_s"] for row in rendered])
    digest = hashlib.sha1(json.dumps({
        "mode": mode, "lines": lines, "preset": preset, "bpm": bpm, "atempo": ATEMPO,
    }, sort_keys=True).encode()).hexdigest()[:8]
    mix_name = f"{name}.vox-{mode}-{digest}.mp3"
    stem_name = f"{name}.vox-{mode}-{digest}.vocals.mp3"
    mix_path = os.path.join(ROOT, "tracks", mix_name)
    stem_path = os.path.join(ROOT, "tracks", stem_name)
    from audio.vocal_mix import mix_lines
    mixed = mix_lines(
        beat_path,
        [{"path": row["path"], "start_s": starts[i]} for i, row in enumerate(rendered)],
        mix_path,
        stem_mp3=stem_path,
    )
    timed = [{
        "text": row["text"], "start_s": round(starts[i], 3), "dur_s": round(row["dur_s"], 3),
    } for i, row in enumerate(rendered)]
    load_s = 0.0
    try:
        load_s = round(float(api.load_seconds()), 2)
    except Exception:
        pass
    _write_sidecar(mix_path, mood, page, mode, preset, timed, stem_name, beat_url, mixed)
    vocals = {
        "state": "ready", "mode": mode, "source": source, "preset": preset["name"],
        "exaggeration": preset["exaggeration"], "cfg_weight": preset["cfg_weight"],
        "temperature": preset["temperature"], "script": lines, "lines": timed,
        "device": device, "model_load_s": load_s, "first_line_s": first_line_s,
        "vocal_render_s": round(time.perf_counter() - started, 2),
        "instrumental_url": beat_url,
        "vocal_stem_url": "/tracks/" + stem_name,
    }
    _note(agent, True, started, mixed.get("duration_s") or 0)
    label = "summary" if mode == "summary" else "song"
    return {
        "vocals": vocals,
        "track": {
            "file": os.path.join("tracks", mix_name),
            "url": "/tracks/" + mix_name,
            "path": mix_path,
            "title": (result["track"].get("title") or "Lofi") + f" · {label}",
            "duration_s": mixed.get("duration_s") or result["track"].get("duration_s"),
            "instrumental_url": beat_url,
            "vocal_stem_url": "/tracks/" + stem_name,
        },
    }


def _line_key(text, preset, seed):
    blob = json.dumps({
        "text": text, "exaggeration": preset["exaggeration"], "cfg_weight": preset["cfg_weight"],
        "temperature": preset["temperature"], "seed": seed, "atempo": ATEMPO, "lofi": True,
    }, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def _write_wav(path, audio, sr):
    import numpy as np
    import scipy.io.wavfile as wf
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wf.write(path, int(sr), np.asarray(audio, dtype=np.float32).reshape(-1))


def _wav_seconds(path):
    import scipy.io.wavfile as wf
    sr, data = wf.read(path)
    frames = data.shape[0] if getattr(data, "ndim", 1) > 1 else len(data)
    return frames / float(sr)


def _treat(raw_wav, out_wav, seed):
    import subprocess
    import numpy as np
    import scipy.io.wavfile as wf
    from audio.lofi_vocals import atempo_chain, lofi_chain, load
    sped = out_wav + ".atempo.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", raw_wav, "-af", atempo_chain(ATEMPO),
         "-ac", "1", "-c:a", "pcm_f32le", sped],
        check=True,
    )
    treated = lofi_chain(load(sped), seed=int(seed) % 100000)
    peak = float(np.abs(treated).max()) if treated.size else 0.0
    if peak > 1:
        treated = treated / peak
    wf.write(out_wav, 44100, np.clip(treated.T, -1, 1).astype(np.float32))
    try:
        os.remove(sped)
    except OSError:
        pass


def _write_sidecar(mix_path, mood, page, mode, preset, lines, stem_name, beat_url, mixed):
    attrs = mood.get("musical_attributes") or {}
    title = ((page or {}).get("title") or "page")[:80]
    label = "summary" if mode == "summary" else "song"
    beat_rel = str(beat_url or "").lstrip("/")
    side = {
        "title": f"URL Lofi: {title} · {label}",
        "label": label,
        "bpm": attrs.get("bpm"),
        "key": attrs.get("key"),
        "duration_s": mixed.get("duration_s"),
        "notes": "Original lofi with optional spoken vocals (Chatterbox default voice).",
        "source_url": (page or {}).get("url") or mood.get("url"),
        "mood_tags": attrs.get("mood_tags"),
        "instruments": attrs.get("primary_instruments"),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "vocals": mode,
        "script": [row["text"] for row in lines],
        "vocal_stem": "../tracks/" + stem_name,
        "instrumental": "../" + beat_rel,
        "vocal_lines": lines,
        "preset": preset["name"],
    }
    tmp = mix_path[:-4] + ".json.part"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(side, handle, indent=2)
    os.replace(tmp, mix_path[:-4] + ".json")


def _note(agent, ok, started, audio_s):
    try:
        from engine import usage
        usage.record(agent or "anonymous", "vocals", bool(ok), time.perf_counter() - started,
                     audio_s=float(audio_s or 0))
    except Exception:
        pass
