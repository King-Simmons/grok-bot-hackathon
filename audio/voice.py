"""audio/voice.py — Chatterbox TTS, built-in default voice only.

Stable interface for other modes (summary, song, lyric alignment, a per-track
voice stem) to call. This module does not write scripts, align to a beat, or
duck music. It turns text into one audio file.

    from audio.voice import synthesize_speech, PRESETS, VoiceUnavailable

    news = PRESETS["news"]  # exaggeration 0.7, cfg_weight 0.3, speed 0.9
    clip = synthesize_speech(text, news["exaggeration"], news["cfg_weight"], news["speed"], out_path)

``clip["path"]`` is the written file. See docs/VOICE.md for the return keys.

Importing this module does not import torch or load the model. If
chatterbox-tts is missing, synthesize_speech raises VoiceUnavailable and
callers keep working.

No reference clip. Do not clone a real person's voice without their consent.
"""
import argparse, hashlib, importlib.util, json, math, os, re, shutil, subprocess, sys, tempfile, threading, time
from typing import TypedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Chatterbox library defaults. PRESETS are the CPU listening pass
# (Python 3.11, torch 2.6.0, chatterbox-tts 0.1.7): the built-in voice
# is a little fast for lofi, so both presets set speed 0.9 (ffmpeg atempo).
DEFAULT_EXAGGERATION = 0.5
DEFAULT_CFG = 0.5
DEFAULT_SPEED = 1.0
PRESETS = {
    "news": {"exaggeration": 0.7, "cfg_weight": 0.3, "speed": 0.9},
    "dj": {"exaggeration": 0.5, "cfg_weight": 0.5, "speed": 0.9},
}
MAX_CHARS = 8000
MAX_CHUNK = 280
CHUNK_GAP_S = 0.2
CACHE_VERSION = 1

_model = None
_lock = threading.RLock()


class VoiceUnavailable(RuntimeError):
    """chatterbox-tts is missing, or the model cannot load. Music still works."""


class SpeechClip(TypedDict):
    """Stable return value of synthesize_speech. Callers can rely on these keys."""
    path: str
    file: str
    url: str | None
    duration_s: float
    sample_rate: int
    exaggeration: float
    cfg_weight: float
    speed: float
    lofi: bool
    cached: bool
    voice: str
    watermark: str
    load_s: float
    synth_s: float


NOT_INSTALLED = (
    "chatterbox-tts is not installed, so synthesize_speech cannot run. "
    "Music synthesis and the browser voice are unaffected. "
    "Use Python 3.11 (chatterbox-tts 0.1.7 does not support Python 3.13; tested with CPU torch 2.6.0): "
    "pip install -r requirements-voice.txt && pip install 'setuptools>=61,<81'. "
    "setuptools 81 and newer crashes resemble-perth while the model loads. "
    "See docs/VOICE.md."
)

__all__ = [
    "synthesize_speech",
    "resolve_controls",
    "SpeechClip",
    "PRESETS",
    "VoiceUnavailable",
    "available",
    "DEFAULT_EXAGGERATION",
    "DEFAULT_CFG",
    "DEFAULT_SPEED",
]


def available() -> bool:
    """True when the chatterbox package is on the path. Does not load the model."""
    try:
        return importlib.util.find_spec("chatterbox") is not None
    except Exception:
        return False


def _python_blocker():
    major, minor = sys.version_info[0], sys.version_info[1]
    if (major, minor) >= (3, 13):
        return (
            f"Python {major}.{minor} cannot run chatterbox-tts 0.1.7. "
            "Use Python 3.11 (tested with CPU torch 2.6.0). See docs/VOICE.md."
        )
    return None


def _setuptools_blocker():
    try:
        import setuptools
        major = int(str(setuptools.__version__).split(".")[0])
    except Exception:
        return None
    if major >= 81:
        return (
            f"setuptools {setuptools.__version__} breaks resemble-perth, so Chatterbox crashes on load. "
            "Pin it: pip install 'setuptools>=61,<81'. See docs/VOICE.md."
        )
    return None


def _check_controls(exaggeration, cfg_weight, speed):
    exaggeration = float(exaggeration)
    cfg_weight = float(cfg_weight)
    speed = float(speed)
    if not math.isfinite(exaggeration) or not 0.0 <= exaggeration <= 2.0:
        raise ValueError("exaggeration must be between 0 and 2")
    if not math.isfinite(cfg_weight) or not 0.0 <= cfg_weight <= 1.0:
        raise ValueError("cfg_weight must be between 0 and 1")
    if not math.isfinite(speed) or not 0.5 <= speed <= 2.0:
        raise ValueError("speed must be between 0.5 and 2 (ffmpeg atempo)")
    return exaggeration, cfg_weight, speed


def resolve_controls(exaggeration=None, cfg_weight=None, speed=None, preset=None):
    """Fill omitted controls. Explicit numbers win over ``preset`` (``news`` or ``dj``).

    Returns ``(exaggeration, cfg_weight, speed)`` ready to pass into synthesize_speech.
    Omitted values use the Chatterbox defaults (0.5, 0.5, speed 1).
    """
    values = {
        "exaggeration": DEFAULT_EXAGGERATION,
        "cfg_weight": DEFAULT_CFG,
        "speed": DEFAULT_SPEED,
    }
    if preset:
        key = str(preset).strip().lower()
        if key not in PRESETS:
            raise ValueError(f"unknown preset {preset!r}; use news or dj")
        values.update(PRESETS[key])
    if exaggeration is not None:
        values["exaggeration"] = exaggeration
    if cfg_weight is not None:
        values["cfg_weight"] = cfg_weight
    if speed is not None:
        values["speed"] = speed
    return _check_controls(values["exaggeration"], values["cfg_weight"], values["speed"])


def split_text(text, limit=MAX_CHUNK):
    """Break a long read into chunks Chatterbox can finish. Whitespace is preserved when rejoined."""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return [text] if text else []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks, buf = [], ""

    def flush():
        nonlocal buf
        if buf:
            chunks.append(buf)
            buf = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        while len(sentence) > limit:
            cut = sentence.rfind(" ", 0, limit + 1)
            if cut < 1:
                cut = limit
            flush()
            chunks.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if not sentence:
            continue
        if buf and len(buf) + 1 + len(sentence) > limit:
            flush()
        buf = f"{buf} {sentence}".strip() if buf else sentence
    flush()
    return chunks


def _get_model():
    """Load Chatterbox once. The built-in voice ships in conds.pt. Never pass a reference clip."""
    global _model
    if _model is not None:
        return _model, 0.0
    with _lock:
        if _model is not None:
            return _model, 0.0
        if not available():
            raise VoiceUnavailable(NOT_INSTALLED)
        blocked = _python_blocker() or _setuptools_blocker()
        if blocked:
            raise VoiceUnavailable(blocked)
        started = time.perf_counter()
        try:
            from chatterbox.tts import ChatterboxTTS
        except Exception as exc:
            raise VoiceUnavailable(
                "chatterbox-tts failed to import "
                f"({type(exc).__name__}: {exc}). "
                "If perth or pkg_resources is mentioned, pin setuptools<81. "
                "chatterbox-tts 0.1.7 needs Python 3.11 and CPU torch 2.6.0. "
                "See docs/VOICE.md."
            ) from exc
        device = os.environ.get("CHATTERBOX_DEVICE", "cpu").strip() or "cpu"
        print(f"Loading Chatterbox default voice on {device} (about 8s the first time)...", file=sys.stderr)
        try:
            _model = ChatterboxTTS.from_pretrained(device=device)
        except Exception as exc:
            raise VoiceUnavailable(
                f"Chatterbox failed to load on {device} ({type(exc).__name__}: {exc}). See docs/VOICE.md."
            ) from exc
        return _model, time.perf_counter() - started


def _to_mono(wav):
    import numpy as np
    if hasattr(wav, "detach"):
        wav = wav.detach().cpu().float().numpy()
    wav = np.asarray(wav, dtype=np.float32)
    if wav.ndim == 2:
        if wav.shape[0] == 1:
            wav = wav[0]
        elif wav.shape[1] == 1:
            wav = wav[:, 0]
        else:
            wav = wav.mean(axis=0)
    return np.ascontiguousarray(wav.reshape(-1), dtype=np.float32)


def _concat(pieces, sample_rate, gap_s):
    import numpy as np
    if not pieces:
        return np.zeros(0, dtype=np.float32)
    if len(pieces) == 1 or gap_s <= 0:
        return np.concatenate([np.asarray(p, dtype=np.float32) for p in pieces])
    gap = np.zeros(int(round(gap_s * sample_rate)), dtype=np.float32)
    out = []
    for i, piece in enumerate(pieces):
        if i:
            out.append(gap)
        out.append(np.asarray(piece, dtype=np.float32))
    return np.concatenate(out)


def _render(text, exaggeration, cfg_weight):
    model, load_s = _get_model()
    chunks = split_text(text)
    pieces = []
    started = time.perf_counter()
    for chunk in chunks:
        try:
            # Built-in default voice only. audio_prompt_path is intentionally absent.
            wav = model.generate(chunk, exaggeration=float(exaggeration), cfg_weight=float(cfg_weight))
        except AssertionError as exc:
            raise VoiceUnavailable(
                "Chatterbox loaded without its built-in default voice (conds.pt). "
                "This module does not take a reference clip."
            ) from exc
        pieces.append(_to_mono(wav))
    audio = _concat(pieces, int(model.sr), CHUNK_GAP_S if len(pieces) > 1 else 0.0)
    return audio, int(model.sr), load_s, time.perf_counter() - started


def _write_wav(path, audio, sample_rate):
    import numpy as np
    import wave
    audio = np.asarray(audio, dtype=np.float32)
    channels = 1 if audio.ndim == 1 else int(audio.shape[1])
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".part"
    with wave.open(tmp, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm.tobytes())
    os.replace(tmp, path)


def _read_wav(path):
    import numpy as np
    import wave
    with wave.open(path, "rb") as handle:
        sample_rate = handle.getframerate()
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32767.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels)
    return np.array(pcm, copy=True), int(sample_rate)


def _wav_duration(path):
    import wave
    with wave.open(path, "rb") as handle:
        sample_rate = handle.getframerate() or 1
        return int(handle.getframerate()), handle.getnframes() / sample_rate


def _apply_speed(audio, sample_rate, speed):
    """Time-stretch with ffmpeg atempo. speed 1 returns the same samples and does not call ffmpeg."""
    import numpy as np
    audio = np.asarray(audio, dtype=np.float32)
    if abs(float(speed) - 1.0) < 1e-3:
        return audio, int(sample_rate)
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("speed needs ffmpeg on PATH (atempo). Leave speed at 1 to skip the stretch.")
    from audio.lofi_vocals import atempo_chain
    chain = atempo_chain(float(speed))
    fd, src = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    dst = src + ".out.wav"
    try:
        _write_wav(src, audio, sample_rate)
        proc = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-af", chain, "-c:a", "pcm_s16le", dst],
            capture_output=True,
        )
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace")[-400:]
            raise RuntimeError(f"ffmpeg atempo failed: {err}")
        stretched, sr = _read_wav(dst)
        if stretched.ndim == 2:
            stretched = stretched.mean(axis=1)
        return np.ascontiguousarray(stretched, dtype=np.float32), int(sr)
    finally:
        for item in (src, dst):
            if os.path.exists(item):
                os.remove(item)


def _peak_limit(audio, peak=0.98):
    import numpy as np
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0:
        return audio
    level = float(np.max(np.abs(audio)))
    if level > peak:
        audio = audio * (peak / level)
    return audio


def _apply_lofi(audio, sample_rate):
    """Band-pass, saturation, and reverb from audio/lofi_vocals.lofi_chain. Returns 44.1 kHz."""
    import numpy as np
    from scipy import signal
    from audio.lofi_vocals import SR, lofi_chain
    audio = np.asarray(audio, dtype=np.float64)
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    if int(sample_rate) != SR:
        n = max(1, int(round(len(audio) * float(SR) / float(sample_rate))))
        audio = signal.resample(audio, n)
    treated = np.asarray(lofi_chain(np.stack([audio, audio])), dtype=np.float32).T
    return _peak_limit(treated), SR


def _write_audio(path, audio, sample_rate):
    if os.path.splitext(path)[1].lower() == ".mp3":
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("mp3 output needs ffmpeg on PATH. Use a .wav path to skip it.")
        fd, src = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        parent = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(parent, exist_ok=True)
        tmp = path + ".part"
        try:
            _write_wav(src, audio, sample_rate)
            proc = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-codec:a", "libmp3lame", "-b:a", "160k", tmp],
                capture_output=True,
            )
            if proc.returncode != 0:
                err = proc.stderr.decode("utf-8", "replace")[-400:]
                raise RuntimeError(f"ffmpeg mp3 encode failed: {err}")
            os.replace(tmp, path)
        finally:
            if os.path.exists(src):
                os.remove(src)
            if os.path.exists(tmp):
                os.remove(tmp)
        return
    _write_wav(path, audio, sample_rate)


def _publish(path):
    path = os.path.abspath(path)
    rel = os.path.relpath(path, ROOT)
    if rel.startswith(".."):
        return path, None
    rel = rel.replace(os.sep, "/")
    return rel, "/" + rel


def _cache_path(text, exaggeration, cfg_weight, speed, lofi):
    key = {
        "v": CACHE_VERSION,
        "text": text,
        "exaggeration": exaggeration,
        "cfg_weight": cfg_weight,
        "speed": speed,
        "lofi": bool(lofi),
    }
    digest = hashlib.sha1(json.dumps(key, sort_keys=True).encode()).hexdigest()[:12]
    return os.path.join(ROOT, "tracks", f"speech-{digest}.wav")


def _result(path, *, exaggeration, cfg_weight, speed, lofi, cached, load_s, synth_s, duration_s=None, sample_rate=None):
    if (duration_s is None or sample_rate is None) and str(path).lower().endswith(".wav"):
        sample_rate, duration_s = _wav_duration(path)
    file_rel, url = _publish(path)
    clip: SpeechClip = {
        "path": os.path.abspath(path),
        "file": file_rel,
        "url": url,
        "duration_s": round(float(duration_s or 0), 2),
        "sample_rate": int(sample_rate or 0),
        "exaggeration": float(exaggeration),
        "cfg_weight": float(cfg_weight),
        "speed": float(speed),
        "lofi": bool(lofi),
        "cached": bool(cached),
        "voice": "chatterbox-default",
        "watermark": "perth",
        "load_s": round(float(load_s), 2),
        "synth_s": round(float(synth_s), 2),
    }
    return clip


def synthesize_speech(text, exaggeration=DEFAULT_EXAGGERATION, cfg_weight=DEFAULT_CFG, speed=DEFAULT_SPEED, out_path=None, *, lofi=False):
    """Synthesize ``text`` with Chatterbox's built-in default voice.

    Positional contract (keep this stable)::

        synthesize_speech(text, exaggeration, cfg_weight, speed, out_path) -> SpeechClip

    text
        Words to speak. Empty raises ValueError. Above 8000 characters raises
        ValueError; call once per line if you need your own timing.
    exaggeration
        Chatterbox emotion amount. 0.5 is the library default. 0.7 is the news-anchor read.
    cfg_weight
        Chatterbox classifier-free guidance. 0.5 is the library default. 0.3 gives the
        news read a little more room.
    speed
        ffmpeg atempo, after synthesis, without a pitch change. 1 keeps the model's
        own pace (no ffmpeg). 0.9 is the lofi pace; the default voice reads a bit fast.
    out_path
        wav or mp3 to overwrite. None writes ``tracks/speech-<hash>.wav`` and returns
        that same file on a later identical call (``cached`` is true). Generation is
        stochastic, so the cache is what keeps a stem from changing under you.
    lofi
        Keyword-only, default false. Run the vocal chain in audio/lofi_vocals.py
        (band-pass about 250 Hz to 3.5 kHz, light saturation, small-room reverb).
        Leave this off for a dry stem and do beat alignment downstream.

    The model takes about 8 seconds to load the first time, then about 2 seconds
    of CPU per second of audio. Pre-generate. Do not call this on the live tuner.

    Raises VoiceUnavailable if chatterbox-tts is not installed or cannot load.
    Raises ValueError for empty text or a control outside its range.
    """
    text = " ".join(str(text or "").split())
    if not text:
        raise ValueError("text is empty")
    if len(text) > MAX_CHARS:
        raise ValueError(
            f"text is too long ({len(text)} characters, max {MAX_CHARS}). "
            "Call synthesize_speech once per section so alignment stays yours."
        )
    exaggeration, cfg_weight, speed = _check_controls(exaggeration, cfg_weight, speed)
    lofi = bool(lofi)
    if out_path:
        path = os.path.abspath(out_path)
        reusable = False
    else:
        path = _cache_path(text, exaggeration, cfg_weight, speed, lofi)
        reusable = True
    if reusable and os.path.isfile(path) and os.path.getsize(path) > 44:
        return _result(path, exaggeration=exaggeration, cfg_weight=cfg_weight, speed=speed,
                       lofi=lofi, cached=True, load_s=0.0, synth_s=0.0)
    with _lock:
        if reusable and os.path.isfile(path) and os.path.getsize(path) > 44:
            return _result(path, exaggeration=exaggeration, cfg_weight=cfg_weight, speed=speed,
                           lofi=lofi, cached=True, load_s=0.0, synth_s=0.0)
        audio, sample_rate, load_s, synth_s = _render(text, exaggeration, cfg_weight)
        audio, sample_rate = _apply_speed(audio, sample_rate, speed)
        if lofi:
            audio, sample_rate = _apply_lofi(audio, sample_rate)
        audio = _peak_limit(audio)
        _write_audio(path, audio, sample_rate)
    duration = (len(audio) / float(sample_rate)) if sample_rate else 0.0
    return _result(path, exaggeration=exaggeration, cfg_weight=cfg_weight, speed=speed, lofi=lofi,
                   cached=False, load_s=load_s, synth_s=synth_s, duration_s=duration, sample_rate=sample_rate)


def main(argv=None):
    """Speak one clip. Scripts and song modes belong in the caller, not here."""
    parser = argparse.ArgumentParser(
        description="Speak text with Chatterbox's built-in default voice. Does not clone a reference clip."
    )
    parser.add_argument("--text", help="Words to speak")
    parser.add_argument("--text-file", help="UTF-8 file of words to speak (one clip, not a timed script)")
    parser.add_argument("--preset", choices=sorted(PRESETS), help="news is 0.7/0.3/speed 0.9; dj is 0.5/0.5/speed 0.9")
    parser.add_argument("--exaggeration", type=float)
    parser.add_argument("--cfg-weight", type=float)
    parser.add_argument("--speed", type=float, help="ffmpeg atempo. 1 is the model's pace; 0.9 is the lofi pace")
    parser.add_argument("--lofi", action="store_true", help="band-pass, saturation, and reverb from audio/lofi_vocals.py")
    parser.add_argument("--out", help="wav or mp3 to overwrite. Omit for a cached tracks/speech-*.wav")
    args = parser.parse_args(argv)
    if args.text and args.text_file:
        print("Pass --text or --text-file, not both.", file=sys.stderr)
        return 2
    if args.text_file:
        with open(args.text_file, encoding="utf-8") as handle:
            text = handle.read()
    elif args.text:
        text = args.text
    else:
        print("Pass --text or --text-file. See docs/VOICE.md.", file=sys.stderr)
        return 2
    try:
        exaggeration, cfg_weight, speed = resolve_controls(args.exaggeration, args.cfg_weight, args.speed, args.preset)
        info = synthesize_speech(text, exaggeration, cfg_weight, speed, args.out, lofi=args.lofi)
    except (VoiceUnavailable, ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
