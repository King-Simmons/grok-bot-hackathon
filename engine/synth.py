"""engine/synth.py - parameterized original lofi generator (~36-42 s). No samples.

Same swung boom-bap form and length as before. The seed picks a progression
(jazzy 7ths/9ths, ii-V-I, modal) and a chord/lead pair, plus a few textures.
Deterministic per seed. render(attrs, seed, out_mp3) -> info dict.
"""
import hashlib, json, os, re, time
import numpy as np, scipy.signal as ss
from scipy.ndimage import minimum_filter1d, uniform_filter1d

SR = 32000  # 32 kHz keeps render fast; master is low-passed at 7.5 kHz anyway
NOTE = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7,
        "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
# Intervals from the chord root. Upper voices are folded into one octave.
QUAL = {
    "maj7": (0, 4, 7, 11), "maj9": (0, 4, 7, 11, 14), "6": (0, 4, 7, 9), "maj7#11": (0, 4, 7, 11, 18),
    "m7": (0, 3, 7, 10), "m9": (0, 3, 7, 10, 14), "m11": (0, 3, 7, 10, 14, 17),
    "7": (0, 4, 7, 10), "9": (0, 4, 7, 10, 14), "13": (0, 4, 7, 10, 21), "7sus": (0, 5, 7, 10),
    "m7b5": (0, 3, 6, 10),
}
SYM = {k: ("m7b5" if k == "m7b5" else k) for k in QUAL}
# (name, tags, [(semitones from tonic, quality), ...]) — one chord per bar of the loop.
def _P(name, tags, chords):
    return (name, tags, tuple(chords))

MINOR_PROGS = [
    _P("i-iv-bVI-v", ("diatonic", "vamp"), [(0, "m9"), (5, "m7"), (8, "maj7"), (7, "m7")]),
    _P("i-bVI-iv-bVII", ("diatonic", "modal"), [(0, "m9"), (8, "maj9"), (5, "m7"), (10, "7")]),
    _P("i-bVII-bVI-iv", ("diatonic", "dark"), [(0, "m7"), (10, "maj7"), (8, "maj7"), (5, "m9")]),
    _P("iiø-V-i-bVI", ("jazz",), [(2, "m7b5"), (7, "9"), (0, "m9"), (8, "maj7")]),
    _P("i-iv-iiø-V", ("jazz",), [(0, "m9"), (5, "m7"), (2, "m7b5"), (7, "9")]),
    _P("i-bIII-bVI-V", ("jazz", "dark"), [(0, "m9"), (3, "maj7"), (8, "maj7"), (7, "7")]),
    _P("i-v-bVI-bVII", ("modal", "diatonic"), [(0, "m7"), (7, "m7"), (8, "maj7"), (10, "7")]),
    _P("i-bVI-bIII-bVII", ("modal", "diatonic"), [(0, "m9"), (8, "maj9"), (3, "maj7"), (10, "9")]),
    _P("i-IV-i-bVII", ("modal", "dorian"), [(0, "m9"), (5, "maj7"), (0, "m7"), (10, "maj7")]),
    _P("iiø-V-i-iv", ("jazz",), [(2, "m7b5"), (7, "7"), (0, "m9"), (5, "m11")]),
    _P("i-bVI-iiø-V", ("jazz",), [(0, "m9"), (8, "maj7"), (2, "m7b5"), (7, "9")]),
    _P("i-IV-bVII-bIII", ("modal", "dorian", "jazz"), [(0, "m9"), (5, "maj9"), (10, "maj7"), (3, "maj7")]),
    _P("i-bVII-iv-V", ("diatonic",), [(0, "m9"), (10, "7"), (5, "m7"), (7, "7sus")]),
    _P("i-v-iv-V", ("diatonic", "jazz"), [(0, "m9"), (7, "m7"), (5, "m9"), (7, "13")]),
    _P("i-bII-bVII-i", ("phrygian", "dark", "modal"), [(0, "m7"), (1, "maj7"), (10, "m7"), (0, "m9")]),
    _P("i-bII-iv-bVII", ("phrygian", "dark"), [(0, "m7"), (1, "maj7"), (5, "m7"), (10, "7")]),
]
MAJOR_PROGS = [
    _P("I-vi-ii-V", ("jazz", "diatonic"), [(0, "maj9"), (9, "m7"), (2, "m9"), (7, "13")]),
    _P("I-iii-vi-IV", ("diatonic", "bright"), [(0, "maj7"), (4, "m7"), (9, "m7"), (5, "maj9")]),
    _P("IV-V-iii-vi", ("diatonic", "bright"), [(5, "maj7"), (7, "9"), (4, "m7"), (9, "m9")]),
    _P("ii-V-I-vi", ("jazz",), [(2, "m9"), (7, "13"), (0, "maj9"), (9, "m7")]),
    _P("I-IV-ii-V", ("jazz", "diatonic", "bright"), [(0, "maj9"), (5, "maj7"), (2, "m7"), (7, "9")]),
    _P("iii-vi-ii-V", ("jazz",), [(4, "m7"), (9, "m9"), (2, "m7"), (7, "13")]),
    _P("I-bVII-IV-I", ("modal", "bright"), [(0, "maj7"), (10, "maj7"), (5, "maj9"), (0, "6")]),
    _P("ii-V-I-IV", ("jazz",), [(2, "m9"), (7, "9"), (0, "maj9"), (5, "maj7")]),
    _P("vi-ii-V-I", ("jazz",), [(9, "m9"), (2, "m7"), (7, "13"), (0, "maj9")]),
    _P("I-vi-IV-V", ("diatonic", "bright"), [(0, "maj7"), (9, "m7"), (5, "maj9"), (7, "7")]),
    _P("I-V/ii-ii-V", ("jazz",), [(0, "maj9"), (9, "7"), (2, "m9"), (7, "13")]),
    _P("I-II-IV-V", ("modal", "bright"), [(0, "maj7#11"), (2, "maj7"), (5, "maj7"), (7, "9")]),
    _P("I-iv-I-V", ("modal", "dark"), [(0, "maj7"), (5, "m7"), (0, "maj9"), (7, "7sus")]),
    _P("I-bVI-bVII-I", ("modal", "dark"), [(0, "maj9"), (8, "maj7"), (10, "9"), (0, "maj7")]),
    _P("IV-iii-vi-ii", ("diatonic", "vamp"), [(5, "maj9"), (4, "m7"), (9, "m7"), (2, "m9")]),
    _P("I-bVII-ii-V", ("jazz", "modal"), [(0, "maj9"), (10, "maj7"), (2, "m9"), (7, "13")]),
]
PROGS = {"minor": MINOR_PROGS, "major": MAJOR_PROGS}
CHORDS = {
    "calm": ["felt piano", "nylon guitar", "warm pad", "muted guitar", "Rhodes"],
    "energetic": ["Rhodes", "vibraphone", "muted guitar", "nylon guitar"],
    "analytical": ["Rhodes", "felt piano", "vibraphone", "muted guitar"],
    "dark": ["warm pad", "felt piano", "Rhodes", "muted guitar"],
    "melancholic": ["felt piano", "nylon guitar", "warm pad", "muted guitar"],
    "upbeat": ["Rhodes", "nylon guitar", "vibraphone", "muted guitar"],
}
LEADS = {
    "calm": ["flute", "vibraphone", "nylon guitar", "felt piano"],
    "energetic": ["vibraphone", "muted guitar", "Rhodes", "nylon guitar"],
    "analytical": ["vibraphone", "Rhodes", "flute", "muted guitar"],
    "dark": ["flute", "felt piano", "vibraphone", "muted guitar"],
    "melancholic": ["flute", "felt piano", "nylon guitar", "vibraphone"],
    "upbeat": ["vibraphone", "nylon guitar", "Rhodes", "muted guitar"],
}
FAMILIES = ("analytical", "upbeat", "dark", "calm", "melancholic", "energetic")
SYNONYM = {"focused": "analytical", "warm": "upbeat", "sunny": "upbeat", "moody": "dark", "cozy": "calm",
           "nostalgic": "melancholic", "wistful": "melancholic", "bouncy": "energetic", "peaceful": "calm"}


def parse_key(key):
    m = re.match(r"\s*([A-G](?:#|b)?)\s*(major|minor|maj|min|m)?", key or "A minor", re.I)
    root = NOTE.get(m.group(1)[0].upper() + m.group(1)[1:], 9) if m else 9
    mode = "major" if m and m.group(2) and m.group(2).lower().startswith("maj") else "minor"
    return root, mode


def _mood(tags, hint):
    for raw in list(tags or []) + ([hint] if hint else []):
        t = str(raw).lower()
        if t in FAMILIES:
            return t
        if t in SYNONYM:
            return SYNONYM[t]
        for fam in FAMILIES:
            if fam in t:
                return fam
    return "analytical"


def progression_pool(mode, mood):
    out = []
    for item in PROGS[mode]:
        tags = set(item[1])
        if mood == "calm" and "phrygian" in tags:
            continue
        if mood in ("dark", "melancholic") and "bright" in tags and "jazz" not in tags and "dark" not in tags:
            continue
        if mood in ("energetic", "upbeat") and "phrygian" in tags:
            continue
        if mood == "upbeat" and "dark" in tags and "jazz" not in tags:
            continue
        out.append(item)
    return out or list(PROGS[mode])


def spell(root, degree, quality):
    return NAMES[(root + int(degree)) % 12] + SYM[quality]


def choose(attrs, seed=0, mood_hint=None):
    """Progression, voices, and textures for this seed. No audio."""
    attrs = attrs or {}
    seed = int(seed or 0) % 100000
    root, mode = parse_key(attrs.get("key") or "A minor")
    raw_tags = attrs.get("mood_tags") or []
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    tags = [str(t).lower() for t in raw_tags]
    if mood_hint:
        tags.append(str(mood_hint).lower())
    instr = [str(i) for i in (attrs.get("primary_instruments") or [])]
    mood = _mood(raw_tags, mood_hint)
    dark = any(t in tags for t in ("dark", "moody", "melancholic", "nostalgic"))
    energetic = any(t in tags for t in ("energetic", "upbeat", "bouncy"))
    calm = "calm" in tags or "cozy" in tags
    pool = progression_pool(mode, mood)
    name, _tags, specs = pool[seed % len(pool)]
    chords_v = list(CHORDS[mood])
    for listed in instr:
        if listed in CHORDS[mood] or (listed in ("Rhodes", "felt piano", "nylon guitar", "vibraphone", "warm pad", "muted guitar") and listed not in chords_v):
            if listed not in chords_v and listed != "flute":
                chords_v.append(listed)
    chord_voice = chords_v[(seed + 1) % len(chords_v)]
    leads = [v for v in LEADS[mood] if v != chord_voice] or list(LEADS[mood])
    lead_voice = leads[(seed + 2) % len(leads)]
    rain = "rain" in instr or (mood in ("calm", "dark", "melancholic") and seed % 4 == 0)
    room = mood in ("calm", "dark", "melancholic") and seed % 2 == 0
    use_pad = chord_voice != "warm pad" and ("warm pad" in instr or (mood in ("calm", "dark", "melancholic") and seed % 3 == 0))
    wobble = round(0.0007 + (seed % 5) * 0.00025, 5)
    textures = ["tape wobble"]
    if rain:
        textures.append("rain")
    if room:
        textures.append("room ambience")
    if "vinyl crackle" in instr:
        textures.append("vinyl crackle")
    return {"mood": mood, "mode": mode, "root": root, "dark": dark, "energetic": energetic, "calm": calm,
            "progression": name, "specs": specs, "degrees": [d for d, _q in specs],
            "chords": [spell(root, d, q) for d, q in specs], "chord_voice": chord_voice, "lead_voice": lead_voice,
            "use_pad": use_pad, "rain": rain, "rain_level": 0.02 if "rain" in instr else 0.012,
            "room": room, "wobble": wobble, "textures": textures}


def mtof(m): return 440 * 2 ** ((np.asarray(m) - 69) / 12)
def _sos(kind, fc, order=2): return ss.butter(order, fc, kind, fs=SR, output="sos")
def lp(x, fc, o=2): return ss.sosfilt(_sos("low", min(fc, SR * 0.45), o), x)
def hp(x, fc, o=2): return ss.sosfilt(_sos("high", fc, o), x)
def bp(x, lo, hi, o=2): return ss.sosfilt(_sos("band", [lo, min(hi, SR * 0.45)], o), x)


def _release(t, dur, release):
    return np.where(t > dur, np.clip(1 - (t - dur) / release, 0, 1), 1)


# ---------------- instruments ----------------
def rhodes(m, dur, vel=0.7):
    f = float(mtof(m)); L = int((dur + 1.4) * SR); t = np.arange(L) / SR
    env = np.exp(-t / (1.1 + 300 / f)) * (1 - np.exp(-t / 0.004)) * _release(t, dur, 0.35)
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
    env = (1 - np.exp(-t / 0.008)) * _release(t, dur, 0.5)
    thump = 0.08 * lp(np.random.default_rng(int(m)).standard_normal(L), 300) * np.exp(-t / 0.02)
    return (out * env + thump) * vel * 0.8


def nylon(m, dur, vel=0.7):
    f = float(mtof(m)); L = int((min(dur, 2.0) + 0.8) * SR); t = np.arange(L) / SR
    out = np.zeros(L)
    for n in range(1, 9):
        fn = f * n
        if fn > SR / 2.2: break
        out += (np.sin(np.pi * n * 0.18) / n) * np.sin(2 * np.pi * fn * t) * np.exp(-t * (1.6 + 1.1 * n))
    env = (1 - np.exp(-t / 0.002)) * _release(t, dur, 0.25)
    return out * env * vel * 1.3


def muted(m, dur, vel=0.7):
    """Palm-muted guitar: same harmonic recipe as nylon, with a fast decay and a tiny fret click."""
    f = float(mtof(m)); L = int((min(dur, 0.4) + 0.12) * SR); t = np.arange(L) / SR
    out = np.zeros(L)
    for n, a in ((1, 1.0), (2, 0.3), (3, 0.14), (5, 0.05)):
        fn = f * n
        if fn > SR / 2.2: break
        out += a * np.sin(2 * np.pi * fn * t) * np.exp(-t * (8.0 + 2.2 * n))
    click = hp(np.random.default_rng(int(m) + 3).standard_normal(L), 1600) * np.exp(-t / 0.006) * 0.1
    return (out + click) * (1 - np.exp(-t / 0.0012)) * vel * 1.05


def vibes(m, dur, vel=0.7):
    f = float(mtof(m)); L = int((dur + 1.6) * SR); t = np.arange(L) / SR
    x = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(2 * np.pi * f * 4.0 * t) * np.exp(-t / 0.3)
    env = (1 - np.exp(-t / 0.003)) * np.exp(-t / 1.6) * _release(t, dur, 0.6)
    return x * env * (1 + 0.25 * np.sin(2 * np.pi * 5.5 * t)) * vel * 0.6


def pad(m, dur, vel=0.7):
    f = float(mtof(m)); L = int((dur + 1.0) * SR); t = np.arange(L) / SR
    out = np.zeros(L)
    for det in (-9, 0, 9):
        fd = f * 2 ** (det / 1200)
        for n in range(1, 6):
            out += np.sin(2 * np.pi * fd * n * t + det) / n
    env = np.clip(t / 0.6, 0, 1) * _release(t, dur, 0.9)
    return lp(out, 1400) * env * vel * 0.25


def flute(m, dur, vel=0.7):
    """Breathy flute: sine plus a quiet noise layer and a slow vibrato. Lead only."""
    f = float(mtof(m)); L = int((dur + 0.3) * SR); t = np.arange(L) / SR
    vib = np.clip((t - 0.12) / 0.2, 0, 1) * 0.005 * np.sin(2 * np.pi * 4.8 * t)
    phase = 2 * np.pi * np.cumsum(f * (1.0 + vib)) / SR
    body = np.sin(phase) + 0.15 * np.sin(2 * phase)
    breath = hp(np.random.default_rng(int(m) + 11).standard_normal(L), 2000) * 0.04
    att = 0.06 if dur > 0.35 else 0.02
    env = np.clip(t / att, 0, 1) * _release(t, dur, 0.12)
    return lp((body + breath) * env, 4800) * vel * 0.48


VOICE = {"Rhodes": rhodes, "felt piano": felt_piano, "nylon guitar": nylon, "vibraphone": vibes,
         "warm pad": pad, "muted guitar": muted, "flute": flute}


def bassnote(m, dur, kind="upright bass"):
    f = float(mtof(m)); L = int((dur + .15) * SR); t = np.arange(L) / SR
    if kind == "sub bass":
        env = (1 - np.exp(-t / 0.01)) * np.clip(1 - (t - dur) / 0.1, 0, 1)
        return np.tanh(1.3 * np.sin(2 * np.pi * f * t)) * env
    env = (1 - np.exp(-t / 0.004)) * np.exp(-t / 0.9) * np.clip(1 - (t - dur) / 0.12, 0, 1)
    x = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(4 * np.pi * f * t) * np.exp(-t / 0.2) + 0.1 * np.sin(6 * np.pi * f * t) * np.exp(-t / 0.08)
    return np.tanh(1.5 * x) * env


def _voice(root, degree, quality):
    """Rootless upper structure in the same register the old diatonic voicing used."""
    pc = (root + int(degree)) % 12
    bass = 36 + pc
    if bass > 45:
        bass -= 12
    notes = []
    for iv in QUAL[quality][1:]:
        n = 54 + (pc + iv) % 12
        if n < 57:
            n += 12
        if n not in notes:
            notes.append(n)
    return sorted(notes[:4]), bass


# ---------------- render ----------------
def render(attrs, seed=0, out_mp3=None, target_s=38.0, mood_hint=None):
    t_start = time.time()
    c = choose(attrs, seed, mood_hint)
    rng = np.random.default_rng(int(seed or 0) % 100000)
    bpm = float(max(60, min(90, (attrs or {}).get("bpm", 74))))
    root = c["root"]
    instr = (attrs or {}).get("primary_instruments") or ["Rhodes", "upright bass", "dusty drums", "vinyl crackle"]
    dark, energetic, calm = c["dark"], c["energetic"], c["calm"]
    chord_name, lead_name = c["chord_voice"], c["lead_voice"]
    chord_v, lead_v = VOICE[chord_name], VOICE[lead_name]
    use_pad = c["use_pad"]
    bass_kind = "sub bass" if "sub bass" in instr else "upright bass"
    drums_on = "dusty drums" in instr or True  # lofi always has a groove; instrument list may omit it
    crackle_amt = 0.07 if "vinyl crackle" in instr else 0.03
    rain_on, rain_level = c["rain"], c["rain_level"]

    BEAT = 60 / bpm; BAR = 4 * BEAT; S16 = BEAT / 4
    SWING = 0.22 if energetic else 0.30
    NBARS = int(max(9, min(16, round(target_s / BAR) + 1)))
    TAIL = 2.0
    N = int((NBARS * BAR + TAIL) * SR)

    def t16(bar, step): return bar * BAR + step * S16 + (step % 2) * SWING * S16

    def add(buf, sig, t, gain=1.0):
        i = int(t * SR); j = min(len(buf), i + len(sig))
        if 0 <= i < len(buf): buf[i:j] += gain * sig[:j - i]

    chords = [_voice(root, deg, qual) for deg, qual in c["specs"]]
    keys = np.zeros(N); padb = np.zeros(N); bass = np.zeros(N); kick = np.zeros(N)
    snare = np.zeros(N); hats = np.zeros(N); lead = np.zeros(N)

    for bar in range(NBARS):
        voic, broot = chords[bar % 4]; last = bar == NBARS - 1
        t0 = bar * BAR - (0.05 if bar else 0)
        dur = 4 * BEAT if last else 2.4 * BEAT
        strum = 0.03 if chord_name in ("nylon guitar", "muted guitar") else 0.018
        for k, m in enumerate(voic):
            add(keys, chord_v(m, dur, 0.5 + 0.05 * k), t0 + k * strum + rng.uniform(0, .006), 0.85)
        if not last and bar >= 2 and not calm:
            for k, m in enumerate(voic[1:]):
                add(keys, chord_v(m, 0.9 * BEAT, 0.33), t16(bar, 10) + k * 0.015, 0.55)
        if use_pad or chord_name == "warm pad":
            for m in voic[:3]:
                add(padb, pad(m - 12 if use_pad else m, BAR * 0.98, 0.6), bar * BAR, 0.9)

    # topline: pentatonic, seeded, chord-tone-biased on strong steps
    pent = [0, 3, 5, 7, 10] if c["mode"] == "minor" else [0, 2, 4, 7, 9]
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

    # drums (one-shots rendered once, reused) — same boom-bap pattern as before
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
        rain = bp(rng.standard_normal(N), 800, 5000) * rain_level
        mix += np.stack([rain, np.roll(rain, 4000)])
    if c["room"]:
        room = bp(rng.standard_normal(N), 180, 1800) * 0.006
        mix += np.stack([room, np.roll(room, 700)])

    mix = np.stack([hp(ch, 30) for ch in mix])
    t = np.arange(N) / SR
    wob = c["wobble"]
    dly = 0.004 + wob * np.sin(2 * np.pi * 0.45 * t) + (wob * 0.1) * np.sin(2 * np.pi * 6.1 * t)
    mix = np.stack([np.interp(t - dly, t, ch) for ch in mix])
    mix = np.stack([lp(ch, 7500, 2) for ch in mix]); mix = np.tanh(mix * 1.2) / 1.2
    fi, fo = int(1.5 * SR), int(3.5 * SR)
    env = np.ones(N); env[:fi] = np.linspace(0, 1, fi) ** 2; env[-fo:] = np.linspace(1, 0, fo) ** 1.5
    mix *= env

    try:
        import pyloudnorm as pyln
        lufs_in = pyln.Meter(SR).integrated_loudness(np.ascontiguousarray(mix.T))
        if not np.isfinite(lufs_in):
            raise ImportError
        mix *= 10 ** ((-14 - lufs_in) / 20)
    except ImportError:
        mix *= 0.5 / max(1e-9, np.sqrt(np.mean(mix ** 2))) * 0.2
    ceil = 10 ** (-1.5 / 20)
    g = np.minimum(1, ceil / np.maximum(np.abs(mix).max(0), 1e-9)); win = int(0.005 * SR)
    g = uniform_filter1d(minimum_filter1d(g, win * 2), win)
    mix = np.clip(mix * g, -ceil, ceil)
    if not np.isfinite(mix).all():
        mix = np.nan_to_num(mix, nan=0.0, posinf=ceil, neginf=-ceil)
    t_synth = time.time() - t_start

    pcm = (np.ascontiguousarray(mix.T) * 32767).astype(np.int16)
    info = {"duration_s": round(N / SR, 1), "bpm": int(bpm), "key": (attrs or {}).get("key"), "sample_rate": SR,
            "chords_degrees": c["degrees"], "chords": c["chords"], "progression": c["progression"],
            "mode": c["mode"], "chord_voice": chord_name, "lead_voice": lead_name, "textures": c["textures"],
            "bass": bass_kind, "audio_sha1": hashlib.sha1(pcm.tobytes()).hexdigest()[:16],
            "render_s": round(t_synth, 2)}
    if out_mp3:
        t1 = time.time()
        os.makedirs(os.path.dirname(os.path.abspath(out_mp3)) or ".", exist_ok=True)
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
            directory = os.path.dirname(os.path.abspath(out_mp3)) or "."
            fd, tmp_mp3 = tempfile.mkstemp(prefix=".enc-", suffix=".mp3", dir=directory)
            os.close(fd)
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav") as w:
                    wf.write(w.name, SR, pcm)
                    subprocess.run(
                        ["ffmpeg", "-y", "-loglevel", "error", "-i", w.name, "-b:a", "160k", tmp_mp3],
                        check=True)
                os.replace(tmp_mp3, out_mp3)
            except BaseException:
                try:
                    os.unlink(tmp_mp3)
                except FileNotFoundError:
                    pass
                raise
        info["encode_s"] = round(time.time() - t1, 2)
    return info


if __name__ == "__main__":
    import sys
    attrs = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {"bpm": 74, "key": "A minor", "primary_instruments": ["Rhodes", "felt piano", "upright bass", "dusty drums", "vinyl crackle"], "mood_tags": ["analytical"]}
    out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/synth-test.mp3"
    print(json.dumps(render(attrs, seed=1, out_mp3=out)))
