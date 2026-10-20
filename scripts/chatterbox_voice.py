"""Shared Chatterbox loader for the host briefing and optional track vocals.

Importing this module does not import torch and does not load the model.
Both callers use Chatterbox's built-in default voice: generate() is never
given an audio prompt, and Resemble's Perth watermark stays on.

Install once, from scripts/requirements-voice.txt (there is no second pin file):
  Python 3.11, CPU torch 2.6.0 first, then that requirements file, then setuptools<81.
"""
import importlib.util, os, sys, threading, time

INSTALL = (
    "Chatterbox is not installed, so vocals stay off and the instrumental still plays. "
    "Use Python 3.11 and scripts/requirements-voice.txt: install torch==2.6.0 torchaudio==2.6.0 "
    "from the CPU index first, then pip install -r scripts/requirements-voice.txt, "
    "then pip install 'setuptools<81'. ./run.sh --voice does those steps."
)

_LOCK = threading.Lock()
_MODEL = None
_DEVICE = None
_LOAD_S = 0.0


class VoiceUnavailable(RuntimeError):
    """Chatterbox cannot run in this interpreter. The beat can still be served."""


def available():
    """True when the chatterbox package is importable. Does not load weights."""
    return importlib.util.find_spec("chatterbox") is not None


def load_seconds():
    """Cold from_pretrained time, kept after the model is warm. 0 if never loaded."""
    return _LOAD_S


def pick_device(requested="auto"):
    req = (requested or "auto").strip().lower()
    if req == "auto":
        env = os.environ.get("CHATTERBOX_DEVICE", "").strip().lower()
        if env:
            req = env
    if req != "auto":
        if req not in ("cpu", "cuda", "mps"):
            raise VoiceUnavailable(f"unknown Chatterbox device {req!r}")
        return req
    import torch
    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


def _check_runtime():
    if sys.version_info >= (3, 13):
        raise VoiceUnavailable(
            "Chatterbox needs Python 3.10–3.12 (tested on 3.11). "
            "This interpreter is %s. %s" % (sys.version.split()[0], INSTALL))
    try:
        import setuptools
        parts = []
        for piece in setuptools.__version__.split(".")[:2]:
            digits = "".join(ch for ch in piece if ch.isdigit())
            parts.append(int(digits or "0"))
        if tuple(parts) >= (81, 0):
            raise VoiceUnavailable(
                "setuptools %s removes pkg_resources and crashes the Perth watermarker. "
                "pip install 'setuptools<81' inside the voice venv." % setuptools.__version__)
    except VoiceUnavailable:
        raise
    except Exception:
        pass


def load_model(device="auto"):
    """Load Chatterbox once. Later calls return the same model and load_s 0.

    Returns (model, device, load_s). load_seconds() still reports the cold load.
    """
    global _MODEL, _DEVICE, _LOAD_S
    with _LOCK:
        if _MODEL is not None:
            return _MODEL, _DEVICE, 0.0
        _check_runtime()
        if not available():
            raise VoiceUnavailable(INSTALL)
        import torch
        from chatterbox.tts import ChatterboxTTS
        requested = pick_device(device)
        t0 = time.perf_counter()
        try:
            model = ChatterboxTTS.from_pretrained(device=requested)
            used = requested
        except Exception:
            if requested == "cpu":
                raise
            model = ChatterboxTTS.from_pretrained(device="cpu")
            used = "cpu"
        _MODEL, _DEVICE = model, used
        _LOAD_S = time.perf_counter() - t0
        # Drop the unused name so a linter-friendly reference remains for torch.
        del torch
        return model, used, _LOAD_S


def synthesize_line(text, exaggeration=0.5, cfg_weight=0.5, temperature=0.7, seed=7, device="auto"):
    """Speak one line with the built-in voice. Returns (float32 mono, sample_rate, info).

    info has load_s (0 when the model was already warm), synth_s, and device.
    No reference clip is passed, so this cannot clone a voice.
    """
    model, used, load_s = load_model(device)
    import numpy as np
    import torch
    t0 = time.perf_counter()
    torch.manual_seed(int(seed))
    wave = model.generate(
        str(text),
        exaggeration=float(exaggeration),
        cfg_weight=float(cfg_weight),
        temperature=float(temperature),
    )
    audio = wave.squeeze(0).detach().cpu().numpy().astype(np.float32).reshape(-1)
    return audio, int(model.sr), {
        "load_s": load_s,
        "synth_s": time.perf_counter() - t0,
        "device": used,
    }


def warm_async(device="auto"):
    """Start a daemon thread that loads the model. No-op when chatterbox is absent."""
    if not available():
        return None

    def go():
        try:
            load_model(device)
        except Exception as exc:
            print(f"chatterbox warm failed: {exc}", file=sys.stderr)

    thread = threading.Thread(target=go, name="chatterbox-warm", daemon=True)
    thread.start()
    return thread
