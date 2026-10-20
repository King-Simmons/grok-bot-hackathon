#!/usr/bin/env python3
"""Lofi vocal chain: align a vocal stem to a beat's BPM, give it a lofi treatment, mix to MP3.

usage: lofi_vocals.py --vocal VOX --beat BEAT --src-bpm 90 --beat-bpm 83 --out out.mp3
       [--src-start 0.0]   seconds into the vocal where its first downbeat is
       [--offset 0.0]      seconds into the beat where that downbeat should land
       [--pitch 0]         semitones (formant-naive resample pitch shift)
       [--vocal-db 0] [--duck-db 3] [--separate]   (--separate: run demucs first and use its vocal stem)
       [--save-stem path.wav] write the treated vocal alone
Only use on audio you own or have rights to.
"""
import argparse, os, subprocess, sys, tempfile
import numpy as np, scipy.signal as ss, scipy.io.wavfile as wf
from scipy.ndimage import minimum_filter1d, uniform_filter1d
SR = 44100

def ffmpeg(*a): subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *a], check=True)
def load(path, filt=None):
    with tempfile.TemporaryDirectory() as d:
        o = os.path.join(d, "x.wav"); args = ["-i", path]
        if filt: args += ["-af", filt]
        ffmpeg(*args, "-ac", "2", "-ar", str(SR), "-c:a", "pcm_f32le", o)
        sr, x = wf.read(o)
    return x.T.astype(np.float64)
def atempo_chain(f):
    parts = []
    while f > 2.0: parts.append(2.0); f /= 2.0
    while f < 0.5: parts.append(0.5); f /= 0.5
    parts.append(f); return ",".join(f"atempo={p:.6f}" for p in parts)
def sos(kind, fc, o=2): return ss.butter(o, fc, kind, fs=SR, output="sos")

def separate(path, workdir):
    subprocess.run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "-d", "cpu",
                    "-o", workdir, path], check=True)
    name = os.path.splitext(os.path.basename(path))[0]
    return os.path.join(workdir, "htdemucs", name, "vocals.wav")

def lofi_chain(v, seed=7):
    rng = np.random.default_rng(seed); n = v.shape[1]; t = np.arange(n) / SR
    v = np.stack([ss.sosfilt(sos("high", 250, 4), c) for c in v])            # band-pass 250 Hz..
    v = np.stack([ss.sosfilt(sos("low", 3500, 4), c) for c in v])            # ..3.5 kHz
    # simple compressor (RMS envelope), then light saturation
    env = np.sqrt(uniform_filter1d(v.mean(0) ** 2, int(0.02 * SR)) + 1e-12)
    thr = np.percentile(env[env > 1e-4], 70) if np.any(env > 1e-4) else 1
    gain = np.where(env > thr, (thr / env) ** 0.5, 1.0); v = v * gain
    v = v / (np.abs(v).max() + 1e-9); v = np.tanh(2.0 * v) / np.tanh(2.0)
    # slight bitcrush: 10-bit quantize + 2x sample hold, blended 35 %
    crush = np.round(v * 512) / 512; crush = np.repeat(crush[:, ::2], 2, axis=1)[:, :n]
    v = 0.65 * v + 0.35 * crush
    # tape wobble
    dly = 0.003 + 0.0008 * np.sin(2 * np.pi * 0.5 * t) + 0.00008 * np.sin(2 * np.pi * 6.0 * t)
    v = np.stack([np.interp(t - dly, t, c) for c in v])
    # small-room reverb (0.45 s decaying-noise IR, stereo)
    out = []
    for s in (31, 32):
        it = np.arange(int(0.45 * SR)) / SR
        ir = ss.sosfilt(sos("low", 4000), np.random.default_rng(s).standard_normal(len(it))) * np.exp(-it / 0.09)
        ir[0] = 0; ir *= 0.25 / np.sqrt((ir ** 2).sum())
        out.append(ss.fftconvolve(v[len(out)], ir)[:n])
    v = v + np.stack(out)
    # a little vinyl dust on the vocal bus
    crk = np.zeros(n); pos = rng.random(n) < 6 / SR
    crk[pos] = rng.standard_normal(pos.sum()); crk = ss.sosfilt(sos("band", [1500, 6000]), crk)
    return v + 0.02 * crk

def main():
    import pyloudnorm as pyln  # mixer only; lofi_chain stays importable without it
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vocal", required=True); ap.add_argument("--beat", required=True)
    ap.add_argument("--src-bpm", type=float, required=True); ap.add_argument("--beat-bpm", type=float, required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--src-start", type=float, default=0.0)
    ap.add_argument("--offset", type=float, default=0.0); ap.add_argument("--pitch", type=float, default=0.0)
    ap.add_argument("--vocal-db", type=float, default=0.0); ap.add_argument("--duck-db", type=float, default=3.0)
    ap.add_argument("--separate", action="store_true"); ap.add_argument("--save-stem")
    a = ap.parse_args()
    vocal_path = a.vocal
    tmp = tempfile.mkdtemp()
    if a.separate: vocal_path = separate(a.vocal, tmp)
    tempo = a.beat_bpm / a.src_bpm            # >1 speeds the vocal up
    filt = [f"atrim=start={a.src_start}", "asetpts=PTS-STARTPTS"]
    if a.pitch:
        r = 2 ** (a.pitch / 12); filt += [f"asetrate={SR * r:.3f}", f"aresample={SR}"]; tempo /= r
    if abs(tempo - 1) > 1e-6: filt.append(atempo_chain(tempo))
    v = load(vocal_path, ",".join(filt)); beat = load(a.beat)
    v = lofi_chain(v)
    off = int(a.offset * SR); n = max(beat.shape[1], off + v.shape[1])
    vb = np.zeros((2, n)); vb[:, off:off + v.shape[1]] = v
    bb = np.zeros((2, n)); bb[:, :beat.shape[1]] = beat
    # level: vocal ~2 dB above beat loudness, then gentle duck of the beat under the vocal
    m = pyln.Meter(SR); lb = m.integrated_loudness(bb.T); lv = m.integrated_loudness(vb.T)
    vb *= 10 ** ((lb + 2 + a.vocal_db - lv) / 20)
    venv = uniform_filter1d(np.abs(vb).mean(0), int(0.15 * SR)); venv /= venv.max() + 1e-9
    bb *= 10 ** (-a.duck_db * np.clip(venv * 3, 0, 1) / 20)
    if a.save_stem: wf.write(a.save_stem, SR, (np.clip(vb / (np.abs(vb).max() + 1e-9) * .89, -1, 1).T * 32767).astype(np.int16))
    mix = bb + vb
    mix *= 10 ** ((-14 - m.integrated_loudness(mix.T)) / 20)
    ceil = 10 ** (-1.5 / 20); win = int(0.005 * SR)
    g = np.minimum(1, ceil / np.maximum(np.abs(mix).max(0), 1e-9))
    mix *= uniform_filter1d(minimum_filter1d(g, 2 * win), win); mix = np.clip(mix, -ceil, ceil)
    wav = os.path.join(tmp, "mix.wav"); wf.write(wav, SR, (mix.T * 32767).astype(np.int16))
    ffmpeg("-i", wav, "-codec:a", "libmp3lame", "-b:a", "192k", a.out)
    print(f"tempo factor {a.beat_bpm / a.src_bpm:.4f}  LUFS {m.integrated_loudness(mix.T):.2f}  "
          f"peak {20 * np.log10(np.abs(mix).max()):.2f} dBFS  dur {n / SR:.2f}s -> {a.out}")
if __name__ == "__main__": main()
