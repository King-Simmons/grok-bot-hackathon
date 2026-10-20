"""engine/synth.py - parameterized original lofi generator (~36-42 s). No samples.

Approach adapted from src/make_beat.py (FM Rhodes, swung boom-bap drums, noise-IR
reverb, crackle, tape wobble, -14 LUFS master), generalized to any key / bpm /
instrument set / mood. Deterministic per seed.

render(attrs, seed, out_mp3) -> info dict. attrs = PRD musical_attributes.
"""
import json, os, re, time
import numpy as np, scipy.signal as ss
from scipy.ndimage import minimum_filter1d, uniform_filter1d

SR = 32000  # 32 kHz keeps render fast; master is low-passed at 7.5 kHz anyway
NOTE = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7,
        "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
MAJOR = [0, 2, 4, 5, 7, 9, 11]; MINOR = [0, 2, 3, 5, 7, 8, 10]
PROGS = {"minor": [[0, 3, 5, 4], [0, 5, 3, 6], [0, 6, 5, 3]],   # i iv bVI v | i bVI iv bVII | i bVII bVI iv
         "major": [[0, 5, 1, 4], [0, 2, 5, 3], [3, 4, 2, 5]]}   # I vi ii V | I iii vi IV | IV V iii vi
KEYS_INSTR = ["Rhodes", "felt piano", "nylon guitar", "vibraphone", "warm pad"]


def parse_key(key):
    m = re.match(r"\s*([A-G](?:#|b)?)\s*(major|minor|maj|min|m)?", key or "A minor", re.I)
    root = NOTE.get(m.group(1)[0].upper() + m.group(1)[1:], 9) if m else 9
    mode = "major" if m and m.group(2) and m.group(2).lower().startswith("maj") else "minor"
    return root, mode


def mtof(m): return 440 * 2 ** ((np.asarray(m) - 69) / 12)
def _sos(kind, fc, order=2): return ss.butter(order, fc, kind, fs=SR, output="sos")
def lp(x, fc, o=2): return ss.sosfilt(_sos("low", fc, o), x)
def hp(x, fc, o=2): return ss.sosfilt(_sos("high", fc, o), x)
def bp(x, lo, hi, o=2): return ss.sosfilt(_sos("band", [lo, hi], o), x)


# ---------------- instruments ----------------
def rhodes(m, dur, vel=0.7):
    f = float(mtof(m)); L = int((dur + 1.4) * SR); t = np.arange(L) / SR
    env = np.exp(-t / (1.1 + 300 / f)) * (1 - np.exp(-t / 0.004))
    env *= np.where(t > dur, np.clip(1 - (t - dur) / 0.35, 0, 1), 1)
    idx = (0.9 + 1.4 * vel) * np.exp(-t / 0.25); out = 0
    for det in (-4, 4):
        fd = f * 2 ** (det / 1200); out = out + np.sin(2 * np.pi * fd * t + idx * np.sin(2 * np.pi * fd * t))
    tine = 0.12 * vel * np.sin(2 * np.pi * f * 7.0 * t) * np.exp(-t / 0.03)
    return (0.5 * out * env * (1 + 0.12 * np.sin(2 * np.pi * 4.2 * t)) + tine * env) * vel


def felt_piano(m, dur, vel=0.7):
    f = float(mtof(m)); L = int((dur + 1.2) * SR); t = np.arange(L) / SR
    out = np.zeros(L)
    for n, a in enumerate([1, .45, .22, .12, .06], 1):
        fn = f * n * (1 + 0.0004 * n * n)
        if fn > SR / 2.2: break
        out += a * np.sin(2 * np.pi * fn * t) * np.exp(-t * (0.9 + 0.7 * n) * (f / 260) ** 0.3)
    env = (1 - np.exp(-t / 0.008)) * np.where(t > dur, np.clip(1 - (t - dur) / 0.5, 0, 1), 1)
    thump = 0.08 * lp(np.random.default_rng(int(m)).standard_normal(L), 300) * np.exp(-t / 0.02)
    return (out * env + thump) * vel * 0.8


def nylon(m, dur, vel=0.7):
    f = float(mtof(m)); L = int((min(dur, 2.0) + 0.8) * SR); t = np.arange(L) / SR
    out = np.zeros(L)
    for n in range(1, 9):
        fn = f * n
        if fn > SR / 2.2: break
        out += (np.sin(np.pi * n * 0.18) / n) * np.sin(2 * np.pi * fn * t) * np.exp(-t * (1.6 + 1.1 * n))
    env = (1 - np.exp(-t / 0.002)) * np.where(t > dur, np.clip(1 - (t - dur) / 0.25, 0, 1), 1)
    return out * env * vel * 1.3


def vibes(m, dur, vel=0.7):
    f = float(mtof(m)); L = int((dur + 1.6) * SR); t = np.arange(L) / SR
    x = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(2 * np.pi * f * 4.0 * t) * np.exp(-t / 0.3)
    env = (1 - np.exp(-t / 0.003)) * np.exp(-t / 1.6) * np.where(t > dur, np.clip(1 - (t - dur) / 0.6, 0, 1), 1)
    return x * env * (1 + 0.25 * np.sin(2 * np.pi * 5.5 * t)) * vel * 0.6


def pad(m, dur, vel=0.7):
    f = float(mtof(m)); L = int((dur + 1.0) * SR); t = np.arange(L) / SR
    out = np.zeros(L)
    for det in (-9, 0, 9):
        fd = f * 2 ** (det / 1200)
        for n in range(1, 6):
            out += np.sin(2 * np.pi * fd * n * t + det) / n
    env = np.clip(t / 0.6, 0, 1) * np.where(t > dur, np.clip(1 - (t - dur) / 0.9, 0, 1), 1)
    return lp(out, 1400) * env * vel * 0.25


VOICE = {"Rhodes": rhodes, "felt piano": felt_piano, "nylon guitar": nylon, "vibraphone": vibes, "warm pad": pad}


def bassnote(m, dur, kind="upright bass"):
    f = float(mtof(m)); L = int((dur + .15) * SR); t = np.arange(L) / SR
    if kind == "sub bass":
        env = (1 - np.exp(-t / 0.01)) * np.clip(1 - (t - dur) / 0.1, 0, 1)
        return np.tanh(1.3 * np.sin(2 * np.pi * f * t)) * env
    env = (1 - np.exp(-t / 0.004)) * np.exp(-t / 0.9) * np.clip(1 - (t - dur) / 0.12, 0, 1)
    x = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(4 * np.pi * f * t) * np.exp(-t / 0.2) + 0.1 * np.sin(6 * np.pi * f * t) * np.exp(-t / 0.08)
    return np.tanh(1.5 * x) * env


# ---------------- render ----------------
def render(attrs, seed=0, out_mp3=None, target_s=38.0, mood_hint=None):
    t_start = time.time()
    rng = np.random.default_rng(seed)
    bpm = float(max(60, min(90, attrs.get("bpm", 74))))
    root, mode = parse_key(attrs.get("key", "A minor"))
    instr = attrs.get("primary_instruments") or ["Rhodes", "upright bass", "dusty drums", "vinyl crackle"]
    tags = [t.lower() for t in attrs.get("mood_tags", [])] + ([mood_hint] if mood_hint else [])
    dark = any(t in tags for t in ("dark", "moody", "melancholic", "nostalgic"))
    energetic = any(t in tags for t in ("energetic", "upbeat", "bouncy"))
    calm = "calm" in tags or "cozy" in tags

    keys_list = [i for i in instr if i in VOICE] or ["Rhodes"]
    chord_v = VOICE[keys_list[0]]
    lead_v = VOICE[keys_list[1]] if len(keys_list) > 1 and keys_list[1] != "warm pad" else (VOICE["vibraphone"] if keys_list[0] == "warm pad" else chord_v)
    use_pad = "warm pad" in instr and keys_list[0] != "warm pad"
    bass_kind = "sub bass" if "sub bass" in instr else "upright bass"
    drums_on = "dusty drums" in instr or True  # lofi always has a groove; instrument list may omit it
    crackle_amt = 0.07 if "vinyl crackle" in instr else 0.03
    rain_on = "rain" in instr

    BEAT = 60 / bpm; BAR = 4 * BEAT; S16 = BEAT / 4
    SWING = 0.22 if energetic else 0.30
    NBARS = int(max(9, min(16, round(target_s / BAR) + 1)))
    TAIL = 2.0
    N = int((NBARS * BAR + TAIL) * SR)

    def t16(bar, step): return bar * BAR + step * S16 + (step % 2) * SWING * S16

    def add(buf, sig, t, gain=1.0):
        i = int(t * SR); j = min(len(buf), i + len(sig))
        if 0 <= i < len(buf): buf[i:j] += gain * sig[:j - i]

    scale = MAJOR if mode == "major" else MINOR
    prog = PROGS[mode][seed % len(PROGS[mode])]

    def chord(deg):
        tones = [scale[(deg + k) % 7] + 12 * ((deg + k) // 7) for k in (0, 2, 4, 6, 8)]  # 1 3 5 7 9 diatonic
        bass_root = 36 + (root + tones[0]) % 12
        if bass_root > 45: bass_root -= 12
        voic = sorted(54 + (root + x - 54) % 12 for x in tones[1:])  # rootless 3-5-7-9 in 54..65
        voic = [v + 12 if v < 57 else v for v in voic]
        return sorted(voic), bass_root

    chords = [chord(d) for d in prog]
    keys = np.zeros(N); padb = np.zeros(N); bass = np.zeros(N); kick = np.zeros(N)
    snare = np.zeros(N); hats = np.zeros(N); lead = np.zeros(N)

    for bar in range(NBARS):
        voic, broot = chords[bar % 4]; last = bar == NBARS - 1
        t0 = bar * BAR - (0.05 if bar else 0)
        dur = 4 * BEAT if last else 2.4 * BEAT
        strum = 0.03 if keys_list[0] == "nylon guitar" else 0.018
        for k, m in enumerate(voic):
            add(keys, chord_v(m, dur, 0.5 + 0.05 * k), t0 + k * strum + rng.uniform(0, .006), 0.85)
        if not last and bar >= 2 and not calm:
            for k, m in enumerate(voic[1:]):
                add(keys, chord_v(m, 0.9 * BEAT, 0.33), t16(bar, 10) + k * 0.015, 0.55)
        if use_pad or keys_list[0] == "warm pad":
            for m in voic[:3]:
                add(padb, pad(m - 12 if use_pad else m, BAR * 0.98, 0.6), bar * BAR, 0.9)

    # topline: pentatonic, seeded, chord-tone-biased on strong steps
    pent = [0, 3, 5, 7, 10] if mode == "minor" else [0, 2, 4, 7, 9]
    pool = sorted({60 + root + p + 12 * o for p in pent for o in (0, 1, 2) if 64 <= 60 + root + p + 12 * o <= 81})
    rhythms = [[(0, 3), (6, 2), (10, 4)], [(2, 2), (6, 3), (12, 3)], [(0, 4), (8, 6)], [(4, 2), (6, 2), (10, 5)], [(0, 3), (3, 3), (8, 4), (14, 2)]]
    if energetic: rhythms.append([(0, 2), (2, 2), (4, 2), (8, 2), (10, 4)])
    mstart, mend = max(3, NBARS // 3), NBARS - 2
    idx = len(pool) // 2
    for bar in range(mstart, mend):
        if bar == mstart + 4 and rng.random() < 0.5: continue  # breathing room
        voic, _ = chords[bar % 4]; ctones = {v % 12 for v in voic}
        for step, ln in rhythms[rng.integers(len(rhythms))]:
            idx = int(np.clip(idx + rng.choice([-2, -1, -1, 0, 1, 1, 2]), 0, len(pool) - 1))
            if step % 4 == 0:  # snap to nearest chord tone
                cand = [i for i, p in enumerate(pool) if p % 12 in ctones]
                if cand: idx = min(cand, key=lambda i: abs(i - idx))
            add(lead, lead_v(pool[idx], ln * S16, 0.42), t16(bar, step))
    d = int(0.75 * BEAT * SR); dl = lead.copy(); lead_lp = lp(lead, 2500)
    for r in range(1, 4): dl[d * r:] += (0.36 ** r) * lead_lp[:N - d * r]
    lead = dl

    # bass
    for bar in range(2, NBARS):
        _, broot = chords[bar % 4]
        if bar == NBARS - 1:
            add(bass, bassnote(broot, 3.5 * BEAT, bass_kind), bar * BAR); continue
        add(bass, bassnote(broot, 1.6 * BEAT, bass_kind), bar * BAR)
        if not dark: add(bass, bassnote(broot + 7, 0.6 * BEAT, bass_kind), t16(bar, 7), 0.7)
        add(bass, bassnote(broot + 12 if (bar % 2 and not dark) else broot, 0.9 * BEAT, bass_kind), t16(bar, 10), 0.8)
        nxt = chords[(bar + 1) % 4][1]
        add(bass, bassnote(nxt - 1 if bar % 2 else nxt + 2, 0.3 * BEAT, bass_kind), t16(bar, 15), 0.5)
    bass = lp(bass, 320 if bass_kind == "sub bass" else 420, 4)

    # drums (one-shots rendered once, reused)
    tk = np.arange(int(.5 * SR)) / SR
    K = np.tanh(1.4 * (np.sin(2 * np.pi * np.cumsum(48 + 75 * np.exp(-tk / 0.035)) / SR) * np.exp(-tk / 0.28)
                       + 0.25 * lp(rng.standard_normal(len(tk)), 1500) * np.exp(-tk / 0.006)))
    ts = np.arange(int(.4 * SR)) / SR
    SN = [lp(0.9 * bp(rng.standard_normal(len(ts)), 900, 4500) * np.exp(-ts / 0.09) + 0.6 * np.sin(2 * np.pi * 185 * ts) * np.exp(-ts / 0.05), 4200) for _ in range(4)]
    th = np.arange(int(.25 * SR)) / SR
    HC = [lp(hp(rng.standard_normal(len(th)), 7000, 4), 11000) * np.exp(-th / 0.025) for _ in range(4)]
    HO = lp(hp(rng.standard_normal(len(th)), 7000, 4), 11000) * np.exp(-th / 0.12)
    kick_pat = [(0, 1.0), (10, 0.85)] if dark else [(0, 1.0), (7, 0.55), (10, 0.85)]
    if energetic: kick_pat += [(8, 0.5)]
    hat_steps = range(0, 16, 4 if dark else 2)
    breakdown = NBARS - 4
    for bar in range(2, NBARS - 1):
        if not drums_on: break
        if bar != breakdown:
            for st, v in kick_pat + ([(15, 0.35)] if bar % 2 else []):
                add(kick, K, t16(bar, st) + rng.uniform(0, .004), v)
        for st in (4, 12):
            add(snare, SN[rng.integers(4)], t16(bar, st) + 0.012 + rng.uniform(0, .006), 0.85 + rng.uniform(-.05, .05))
        add(snare, SN[rng.integers(4)], t16(bar, 13), 0.12)
        for st in hat_steps:
            add(hats, HC[rng.integers(4)], t16(bar, st) + rng.uniform(0, .005), (0.55 if st % 4 == 0 else 0.4) * rng.uniform(.8, 1))
        if not dark:
            for st in (7, 15): add(hats, HC[rng.integers(4)], t16(bar, st), 0.18)
        if bar % 4 == 3: add(hats, HO, t16(bar, 14), 0.3)
    add(kick, K, (NBARS - 1) * BAR, 0.9)

    # reverb: one shared send, convolved once (fast)
    tir = np.arange(int(2.0 * SR)) / SR
    def ir(s): return lp(np.random.default_rng(s).standard_normal(len(tir)), 5000) * np.exp(-tir / 0.4) * 0.03
    keys_f = lp(keys, 3600 if dark else 4200); lead_f = lp(lead, 3200)
    send = keys_f * 0.35 + lead_f * 0.35 + padb * 0.5 + snare * 0.18
    wet = np.stack([ss.fftconvolve(send, ir(11))[:N], ss.fftconvolve(send, ir(12))[:N]])
    def pan(x, p): return np.stack([x * np.sqrt(2 * (1 - p)), x * np.sqrt(2 * p)]) / np.sqrt(2)
    mix = (pan(keys_f, .45) * 0.55 + pan(lead_f, .6) * 0.35 + pan(padb, .5) * 0.35 + wet
           + pan(bass, .5) * 0.8 + pan(kick, .5) * 0.9 + pan(snare, .52) * 0.45 + pan(lp(hats, 9000), .6) * 0.22)

    crk = np.zeros(N); pos = rng.random(N) < 18 / SR
    crk[pos] = rng.standard_normal(pos.sum()) * rng.uniform(.2, 1, pos.sum())
    crk = bp(crk, 1000, 7000) * 0.5
    big = rng.random(N) < 0.6 / SR; crk[big] += rng.choice([-1, 1], big.sum()) * 0.6
    hiss = lp(hp(rng.standard_normal(N), 2000), 8000) * 0.004
    mix += np.stack([crk * crackle_amt + hiss, np.roll(crk, 37) * crackle_amt + hiss[::-1]])
    if rain_on:
        rain = bp(rng.standard_normal(N), 800, 5000) * 0.02
        mix += np.stack([rain, np.roll(rain, 4000)])

    mix = np.stack([hp(c, 30) for c in mix])
    t = np.arange(N) / SR
    dly = 0.004 + 0.0012 * np.sin(2 * np.pi * 0.45 * t) + 0.00012 * np.sin(2 * np.pi * 6.1 * t)
    mix = np.stack([np.interp(t - dly, t, c) for c in mix])
    mix = np.stack([lp(c, 7500, 2) for c in mix]); mix = np.tanh(mix * 1.2) / 1.2
    fi, fo = int(1.5 * SR), int(3.5 * SR)
    env = np.ones(N); env[:fi] = np.linspace(0, 1, fi) ** 2; env[-fo:] = np.linspace(1, 0, fo) ** 1.5
    mix *= env

    try:
        import pyloudnorm as pyln
        lufs_in = pyln.Meter(SR).integrated_loudness(mix.T)
        mix *= 10 ** ((-14 - lufs_in) / 20)
    except ImportError:
        mix *= 0.5 / max(1e-9, np.sqrt(np.mean(mix ** 2))) * 0.2
    ceil = 10 ** (-1.5 / 20)
    g = np.minimum(1, ceil / np.maximum(np.abs(mix).max(0), 1e-9)); win = int(0.005 * SR)
    g = uniform_filter1d(minimum_filter1d(g, win * 2), win)
    mix = np.clip(mix * g, -ceil, ceil)
    t_synth = time.time() - t_start

    pcm = (mix.T * 32767).astype(np.int16)
    info = {"duration_s": round(N / SR, 1), "bpm": int(bpm), "key": attrs.get("key"), "sample_rate": SR,
            "chords_degrees": prog, "mode": mode, "chord_voice": keys_list[0], "bass": bass_kind,
            "render_s": round(t_synth, 2)}
    if out_mp3:
        t1 = time.time()
        os.makedirs(os.path.dirname(os.path.abspath(out_mp3)), exist_ok=True)
        try:
            import lameenc
            enc = lameenc.Encoder(); enc.set_bit_rate(160); enc.set_in_sample_rate(SR)
            enc.set_channels(2); enc.set_quality(5)
            data = enc.encode(pcm.tobytes()) + enc.flush()
            tmp = out_mp3 + ".part"
            with open(tmp, "wb") as f: f.write(data)
            os.replace(tmp, out_mp3)
        except ImportError:  # fall back to ffmpeg
            import subprocess, tempfile, scipy.io.wavfile as wf
            with tempfile.NamedTemporaryFile(suffix=".wav") as w:
                wf.write(w.name, SR, pcm)
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", w.name, "-b:a", "160k", out_mp3], check=True)
        info["encode_s"] = round(time.time() - t1, 2)
    return info


if __name__ == "__main__":
    import sys
    attrs = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {"bpm": 74, "key": "A minor", "primary_instruments": ["Rhodes", "felt piano", "upright bass", "dusty drums", "vinyl crackle"], "mood_tags": ["analytical"]}
    out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/synth-test.mp3"
    print(json.dumps(render(attrs, seed=1, out_mp3=out)))
