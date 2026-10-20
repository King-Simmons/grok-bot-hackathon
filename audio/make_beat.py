"""Zlatan Beat 001 - fully synthesized original lofi instrumental. No samples."""
import os
import numpy as np, scipy.signal as ss, scipy.io.wavfile as wf, pyloudnorm as pyln
SR = 44100
rng = np.random.default_rng(1)
BPM = 72.0
BEAT = 60 / BPM; BAR = 4 * BEAT; S16 = BEAT / 4
SWING = 0.30  # fraction of a 16th that odd 16ths are delayed
NBARS = 13
TAIL = 2.2
N = int((NBARS * BAR + TAIL) * SR)
def mtof(m): return 440 * 2 ** ((m - 69) / 12)
def t16(bar, step): return bar * BAR + step * S16 + (step % 2) * SWING * S16
def lp(x, fc, order=2): return ss.sosfilt(ss.butter(order, fc, 'low', fs=SR, output='sos'), x)
def hp(x, fc, order=2): return ss.sosfilt(ss.butter(order, fc, 'high', fs=SR, output='sos'), x)
def bp(x, lo, hi, order=2): return ss.sosfilt(ss.butter(order, [lo, hi], 'band', fs=SR, output='sos'), x)
def add(buf, sig, t, gain=1.0):
    i = int(t * SR); j = min(len(buf), i + len(sig))
    if i < len(buf): buf[i:j] += gain * sig[:j - i]

# ---------- Rhodes-ish FM electric piano ----------
def rhodes(m, dur, vel=0.7):
    f = mtof(m); L = int((dur + 1.6) * SR); t = np.arange(L) / SR
    env = np.exp(-t / (1.1 + 300 / f)) * (1 - np.exp(-t / 0.004))
    rel = np.clip(1 - (t - dur) / 0.35, 0, 1); env *= np.where(t > dur, rel, 1)
    idx = (0.9 + 1.4 * vel) * np.exp(-t / 0.25)
    out = 0
    for det in (-4, 4):  # cents detune, two voices
        fd = f * 2 ** (det / 1200)
        mod = idx * np.sin(2 * np.pi * fd * t)
        out = out + np.sin(2 * np.pi * fd * t + mod)
    tine = 0.12 * vel * np.sin(2 * np.pi * f * 7.0 * t) * np.exp(-t / 0.03)
    trem = 1 + 0.12 * np.sin(2 * np.pi * 4.2 * t)
    return (0.5 * out * env * trem + tine * env) * vel

# rootless jazz voicings (Ab major / F minor)
CHORDS = [  # name, rhodes voicing, bass root
    ("Fm9",    [56, 60, 63, 67], 41),   # Ab C Eb G   over F
    ("Bbm9",   [56, 61, 65, 72], 46),   # Ab Db F C   over Bb
    ("Eb9",    [55, 61, 65, 70], 39),   # G Db F Bb   over Eb
    ("Abmaj9", [55, 60, 63, 70], 44),   # G C Eb Bb   over Ab
]
keys = np.zeros(N); bass = np.zeros(N); kick = np.zeros(N); snare = np.zeros(N)
hats = np.zeros(N); lead = np.zeros(N)

for bar in range(NBARS):
    name, voic, root = CHORDS[bar % 4]
    last = bar == NBARS - 1
    # main hit: slight anticipation on bars after the first, strummed
    t0 = bar * BAR - (0.06 if bar else 0)
    dur = 4 * BEAT if last else 2.4 * BEAT
    for k, m in enumerate(voic):
        add(keys, rhodes(m, dur, 0.55 + 0.05 * k), t0 + k * 0.018 + rng.uniform(0, .006), 0.9)
    if not last and bar >= 2:  # soft re-hit on swung "and" of 3
        for k, m in enumerate(voic[1:]):
            add(keys, rhodes(m, 0.9 * BEAT, 0.35), t16(bar, 10) + k * 0.015, 0.6)

# simple pentatonic-ish topline (Ab major pent / F minor pent), bars 5-11
MEL = {5: [(0, 75, 3), (6, 72, 2), (10, 70, 4)],
       6: [(2, 72, 2), (6, 73, 3), (12, 72, 3)],
       7: [(0, 70, 4), (8, 67, 6)],
       8: [(4, 68, 2), (6, 70, 2), (10, 72, 5)],
       9: [(0, 75, 3), (6, 77, 2), (10, 75, 4)],
       10:[(2, 73, 3), (8, 72, 6)],
       11:[(0, 70, 4), (8, 72, 2), (10, 67, 6)]}
for bar, notes in MEL.items():
    for step, m, ln in notes:
        add(lead, rhodes(m, ln * S16, 0.45), t16(bar, step))
# tape-ish feedback delay on lead (dotted 8th)
d = int(0.75 * BEAT * SR); dl = lead.copy()
for r in range(1, 5): dl[d * r:] += (0.38 ** r) * lp(lead, 2500)[:N - d * r]
lead = dl

# ---------- Bass ----------
def bassnote(m, dur):
    f = mtof(m); L = int((dur + .15) * SR); t = np.arange(L) / SR
    env = (1 - np.exp(-t / 0.008)) * np.exp(-t / 1.2) * np.clip(1 - (t - dur) / 0.12, 0, 1)
    x = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(4 * np.pi * f * t)
    return np.tanh(1.6 * x) * env
for bar in range(2, NBARS):
    name, voic, root = CHORDS[bar % 4]
    if bar == NBARS - 1:
        add(bass, bassnote(root, 3.5 * BEAT), bar * BAR); continue
    add(bass, bassnote(root, 1.6 * BEAT), bar * BAR)
    add(bass, bassnote(root + 7, 0.6 * BEAT), t16(bar, 7), 0.75)
    add(bass, bassnote(root + 12 if bar % 2 else root, 0.9 * BEAT), t16(bar, 10), 0.8)
    nxt = CHORDS[(bar + 1) % 4][2]
    add(bass, bassnote(nxt - 1 if bar % 2 else nxt + 2, 0.3 * BEAT), t16(bar, 15), 0.55)  # approach note
bass = lp(bass, 380, 4)

# ---------- Drums ----------
def kick_s():
    t = np.arange(int(.5 * SR)) / SR
    f = 48 + 75 * np.exp(-t / 0.035)
    ph = 2 * np.pi * np.cumsum(f) / SR
    x = np.sin(ph) * np.exp(-t / 0.28) + 0.25 * lp(rng.standard_normal(len(t)), 1500) * np.exp(-t / 0.006)
    return np.tanh(1.4 * x)
def snare_s():
    t = np.arange(int(.4 * SR)) / SR
    n = bp(rng.standard_normal(len(t)), 900, 4500) * np.exp(-t / 0.09)
    tone = np.sin(2 * np.pi * 185 * t) * np.exp(-t / 0.05)
    return lp(0.9 * n + 0.6 * tone, 4200)
def hat_s(open_=False):
    t = np.arange(int(.25 * SR)) / SR
    return lp(hp(rng.standard_normal(len(t)), 7000, 4), 11000) * np.exp(-t / (0.12 if open_ else 0.025))
K, Sn = kick_s(), snare_s()
for bar in range(2, NBARS - 1):
    breakdown = bar == 9  # drop the kick for one bar
    if not breakdown:
        for st, v in [(0, 1.0), (7, 0.55), (10, 0.85)] + ([(15, 0.35)] if bar % 2 else []):
            add(kick, K, t16(bar, st) + rng.uniform(0, .004), v)
    for st in (4, 12):
        add(snare, snare_s(), t16(bar, st) + 0.012 + rng.uniform(0, .006), 0.85 + rng.uniform(-.05, .05))
    add(snare, snare_s(), t16(bar, 13), 0.12)  # ghost
    for st in range(0, 16, 2):
        add(hats, hat_s(), t16(bar, st) + rng.uniform(0, .005), (0.55 if st % 4 == 0 else 0.4) * rng.uniform(.8, 1))
    for st in (7, 15):
        add(hats, hat_s(), t16(bar, st), 0.18)
    if bar % 4 == 3: add(hats, hat_s(True), t16(bar, 14), 0.3)
# last bar: single kick + crash-ish soft hit on 1
add(kick, K, (NBARS - 1) * BAR, 0.9)

# ---------- Reverb (generated noise IR, stereo) ----------
def ir(seconds, seed):
    r = np.random.default_rng(seed); t = np.arange(int(seconds * SR)) / SR
    return lp(r.standard_normal(len(t)), 5000) * np.exp(-t / (seconds / 5)) * 0.03
IRL, IRR = ir(2.2, 11), ir(2.2, 12)
def verb(x): return np.stack([ss.fftconvolve(x, IRL)[:N], ss.fftconvolve(x, IRR)[:N]])
def pan(x, p): return np.stack([x * np.sqrt(1 - p), x * np.sqrt(p)]) * 1.414 / 2 * 2 ** .5 / 1.414 * 1.414

keys_f = lp(keys, 3800)
lead_f = lp(lead, 3200)
mix = (pan(keys_f, .45) * 0.55 + verb(keys_f) * 0.35
       + pan(lead_f, .6) * 0.35 + verb(lead_f) * 0.35
       + pan(bass, .5) * 0.8
       + pan(kick, .5) * 0.9
       + pan(snare, .52) * 0.45 + verb(snare) * 0.18
       + pan(lp(hats, 9000), .6) * 0.22)

# vinyl crackle + hiss
crk = np.zeros(N); pos = rng.random(N) < 18 / SR  # ~18 pops/sec
crk[pos] = rng.standard_normal(pos.sum()) * rng.uniform(.2, 1, pos.sum())
crk = bp(crk, 1000, 7000) * 0.5
big = rng.random(N) < 0.6 / SR; crk[big] += rng.choice([-1, 1], big.sum()) * 0.6
hiss = lp(hp(rng.standard_normal(N), 2000), 8000) * 0.004
mix += np.stack([crk * 0.05 + hiss, np.roll(crk, 37) * 0.05 + hiss[::-1]])
# rumble-free
mix = np.stack([hp(c, 30) for c in mix])

# tape wobble: modulated delay (wow ~0.5 Hz, flutter ~6 Hz)
t = np.arange(N) / SR
dly = 0.004 + 0.0012 * np.sin(2 * np.pi * 0.45 * t) + 0.00012 * np.sin(2 * np.pi * 6.1 * t)
mix = np.stack([np.interp(t - dly, t, c) for c in mix])

# master warmth: low-pass + gentle saturation
mix = np.stack([lp(c, 7500, 2) for c in mix])
mix = np.tanh(mix * 1.2) / 1.2

# fades
fi, fo = int(1.5 * SR), int(3.5 * SR)
env = np.ones(N); env[:fi] = np.linspace(0, 1, fi) ** 2; env[-fo:] = np.linspace(1, 0, fo) ** 1.5
mix *= env

# loudness -> -14 LUFS, then soft limit peaks to ~ -1.5 dBFS
meter = pyln.Meter(SR)
mix *= 10 ** ((-14 - meter.integrated_loudness(mix.T)) / 20)
ceil = 10 ** (-1.5 / 20)
g = np.minimum(1, ceil / np.maximum(np.abs(mix).max(0), 1e-9))
win = int(0.005 * SR)
g = ss.minimum_filter1d(g, win * 2) if hasattr(ss, 'minimum_filter1d') else g
from scipy.ndimage import minimum_filter1d, uniform_filter1d
g = uniform_filter1d(minimum_filter1d(g, win * 2), win)
mix *= g; mix = np.clip(mix, -ceil, ceil)
print("LUFS", meter.integrated_loudness(mix.T), "peak dBFS", 20 * np.log10(np.abs(mix).max()), "dur", N / SR)
wf.write(os.path.join(os.path.dirname(os.path.abspath(__file__)), "beat-001.wav"), SR, (mix.T * 32767).astype(np.int16))
