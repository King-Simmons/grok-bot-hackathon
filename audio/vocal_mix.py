"""Place treated vocal lines on a beat, duck the beat, write a mix and a stem.

The lofi tone lives in audio/lofi_vocals.py. This module only aligns, levels, and encodes.
"""
import os, subprocess, tempfile
import numpy as np
from scipy.ndimage import minimum_filter1d, uniform_filter1d

SR = 44100


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def _load(path):
    from audio.lofi_vocals import load
    return load(path)


def _loudness(samples):
    """samples is (n, ch) float. LUFS when pyloudnorm is present, else an RMS stand-in."""
    try:
        import pyloudnorm as pyln
        meter = pyln.Meter(SR)
        value = meter.integrated_loudness(np.asarray(samples, dtype=np.float64))
        if np.isfinite(value):
            return float(value)
    except Exception:
        pass
    rms = float(np.sqrt(np.mean(np.square(samples)) + 1e-12))
    return 20.0 * np.log10(rms + 1e-12)


def _encode(samples, path):
    """samples is (2, n) in about -1..1."""
    import scipy.io.wavfile as wf
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    pcm = np.clip(samples.T, -1, 1)
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "mix.wav")
        wf.write(wav, SR, (pcm * 32767).astype(np.int16))
        _ffmpeg("-i", wav, "-codec:a", "libmp3lame", "-b:a", "192k", path)


def mix_lines(beat_path, lines, out_mp3, stem_mp3=None, duck_db=3.0):
    """Mix mono/stereo line clips onto a beat.

    lines: [{"path", "start_s"}]. Vocal sits about 2 dB above the beat.
    The beat ducks by duck_db under the vocal envelope. Master lands near -14 LUFS.
    Returns duration_s, peak_db, and duck_ratio (mean duck gain where the vocal is present).
    """
    beat = _load(beat_path)
    n = beat.shape[1]
    placed = []
    fade = int(0.008 * SR)
    for line in lines:
        clip = _load(line["path"])
        if clip.shape[1] > fade * 2:
            ramp = np.linspace(0, 1, fade)
            clip = clip.copy()
            clip[:, :fade] *= ramp
            clip[:, -fade:] *= ramp[::-1]
        start = max(0, int(float(line.get("start_s") or 0) * SR))
        n = max(n, start + clip.shape[1])
        placed.append((clip, start))

    music = np.zeros((2, n), dtype=np.float64)
    vocal = np.zeros((2, n), dtype=np.float64)
    music[:, :beat.shape[1]] = beat
    for clip, start in placed:
        vocal[:, start:start + clip.shape[1]] += clip

    music_lufs = _loudness(music.T)
    vocal_lufs = _loudness(vocal.T)
    if np.isfinite(music_lufs) and np.isfinite(vocal_lufs):
        vocal *= 10 ** ((music_lufs + 2.0 - vocal_lufs) / 20.0)

    envelope = uniform_filter1d(np.abs(vocal).mean(0), max(1, int(0.15 * SR)))
    peak_env = float(envelope.max()) if envelope.size else 0.0
    if peak_env > 0:
        envelope = envelope / peak_env
    amount = np.clip(envelope * 3, 0, 1)
    gain = 10 ** (-float(duck_db) * amount / 20.0)
    active = envelope > 0.05
    duck_ratio = float(gain[active].mean()) if np.any(active) else 1.0
    music *= gain

    if stem_mp3:
        stem = vocal.copy()
        stem_peak = float(np.abs(stem).max()) if stem.size else 0.0
        if stem_peak > 1e-6:
            stem *= 0.89 / stem_peak
        _encode(stem, stem_mp3)

    mix = music + vocal
    mix_lufs = _loudness(mix.T)
    if np.isfinite(mix_lufs):
        mix *= 10 ** ((-14.0 - mix_lufs) / 20.0)
    ceiling = 10 ** (-1.5 / 20.0)
    window = max(1, int(0.005 * SR))
    limiter = np.minimum(1, ceiling / np.maximum(np.abs(mix).max(0), 1e-9))
    mix *= uniform_filter1d(minimum_filter1d(limiter, 2 * window), window)
    mix = np.clip(mix, -ceiling, ceiling)
    _encode(mix, out_mp3)
    peak = float(np.abs(mix).max()) if mix.size else 0.0
    return {
        "duration_s": round(n / SR, 3),
        "peak_db": round(20 * np.log10(peak + 1e-12), 2),
        "duck_ratio": round(duck_ratio, 4),
    }
