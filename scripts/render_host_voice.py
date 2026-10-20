#!/usr/bin/env python3
"""Render the 101.5 NEWS TALK host briefing with Chatterbox TTS (Resemble AI).

Builds the exact briefing the dashboard host says (mirrors buildBriefing()/takeFor()/cleanTitle() in
dashboard/index.html as of main 4a332c2), renders each segment with Chatterbox's built-in default
voice (no cloning), joins segments with short pauses, loudness-normalizes, exports
MP3 and writes an episode.json-compatible snippet with podcast_audio + podcast_cues.

Usage:
  python scripts/render_host_voice.py dashboard/headlines.json \
      --out dashboard/host_briefing.mp3 --cues dashboard/podcast_cues.json \
      [--audio-path host_briefing.mp3] [--part none|auto|morning|afternoon|evening|night] \
      [--exaggeration 0.4 --cfg-weight 0.35 --temperature 0.7 --seed 7] \
      [--max-segments N] [--merge-episode dashboard/episode.json] \
      [--x-news dashboard/x_news.json --x-count 3]

With --x-news, the top --x-count X stories (title + the first sentence or two of X's summary) are read
after the headlines, before the outro, and their x_id list goes into episode.json "podcast_x".

Re-run whenever headlines.json changes. CPU render speed ~4-5 s per second of audio.
"""
import argparse, json, re, subprocess, sys, tempfile, time, os
from datetime import datetime
from pathlib import Path

TZ = "America/Chicago"
TAKES = ["Worth keeping an eye on.", "We'll keep you posted on that one.",
         "File that one under good to know.", "More on that as it develops."]
LEADS = ["First up", "Next", "After that", "Also on the wire"]

# ---- briefing text: line-for-line port of the dashboard JS --------------------
def clean_title(t):
    t = re.sub(r"^\s*placeholder headline:\s*", "", str(t or ""), flags=re.I)
    return re.sub(r"\s*\[(video|pdf|audio|\d{4})\]\s*", " ", t, flags=re.I).strip()

def sentence(t):
    t = str(t or "").strip()
    if not t: return ""
    t = t[0].upper() + t[1:]
    return t if re.search(r"[.!?]$", t) else t + "."

def take_for(h, i):
    if h.get("take"): return sentence(h["take"])
    is_hn = isinstance(h.get("score"), (int, float)) and not isinstance(h.get("score"), bool)
    if h.get("summary") and not is_hn: return sentence(h["summary"])
    if is_hn:  # Hacker News items: API facts, then the optional summary
        d = h.get("domain")
        site = "a Hacker News text post" if (not d or d == "news.ycombinator.com") else d.replace(".", " dot ")
        sc = h["score"]; pts = f"{sc} point{'' if sc == 1 else 's'}"
        cmn = h.get("comments"); cm = f"{cmn} comment{'' if cmn == 1 else 's'}" if isinstance(cmn, (int, float)) and not isinstance(cmn, bool) else ""
        return [f"That's from {site}, at {pts}{f' and {cm}' if cm else ''}.",
                f"{pts[0].upper()+pts[1:]} so far{f', with {cm} in the thread' if cm else ''}.",
                f"The link goes to {site}.{f' {cm[0].upper()+cm[1:]} and counting.' if cm else ''}"][i % 3] \
            + (" " + sentence(h["summary"]) if h.get("summary") else "")
    t = clean_title(h.get("title")); c = t.find(", ")
    if c > 0 and c < len(t) - 6:
        r = t[c + 2:].strip()
        return f"In short, {r if re.search(r'[.!?]$', r) else r + '.'}"
    return TAKES[i % len(TAKES)]

def part_of_day(part):
    if part != "auto": return part  # "none" -> time-neutral greeting
    from zoneinfo import ZoneInfo
    hr = datetime.now(ZoneInfo(TZ)).hour % 24
    return "night" if hr < 5 else "morning" if hr < 12 else "afternoon" if hr < 17 else "evening"

def build_briefing(data, part="auto"):
    headlines = [h for h in (data.get("headlines") or []) if h and h.get("title")]
    hl_example = bool(data.get("example") or any(h.get("example") for h in headlines))
    hs = headlines[:8]; n = len(hs)
    srcs = list(dict.fromkeys(h.get("source") for h in hs if h.get("source")))
    one = srcs[0] if len(srcs) == 1 else None
    p = part_of_day(part)
    hello = "Welcome" if p == "none" else f"Good {p}, and welcome"  # a recording can't know when it will be played
    segs = [{"hl": -1, "text": f"{hello} to one oh one point five, News Talk, on Lofi News Radio. "
             + (f"I've got {n} headline{'s' if n > 1 else ''} for you{f', straight from the top of {one}' if one and not hl_example else ''}, so settle in with the beat." if n else "")
             + (" Quick heads-up: these are placeholder headlines while our news bot gets wired up." if hl_example else "")}]
    if not n:
        segs.append({"hl": -1, "text": "The news desk is empty right now, so let's just ride the beat for a bit."})
    for i, h in enumerate(hs):
        lead = "And finally" if (i == n - 1 and n > 1) else LEADS[min(i, len(LEADS) - 1)]
        t = clean_title(h.get("title"))
        main = t[:t.find(", ")] if t.find(", ") > 0 else t
        src = f", from {h['source']}" if h.get("source") and not one else ""
        segs.append({"hl": i, "text": f"{lead}{src}. {sentence(main)} {take_for(h, i)}"})
    segs.append({"hl": -1, "text": "That's the briefing. You've been listening to News Talk on Lofi News Radio. Back to the beat."})
    return segs

# ---- X stories (x_news.json): mirrors xSegments()/xTrim() in index.html ---------
X_WHEN = {"morning": "this morning", "afternoon": "this afternoon", "evening": "tonight", "night": "tonight", "none": ""}
ABBR = re.compile(r"(?:\b[A-Z]\.){1,3}$|\b(?:Mr|Mrs|Ms|Dr|St|vs|Inc|Jr|Sr|Co|Corp|Ltd)\.$")

def split_sentences(t):
    out = []
    for p in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'\u201c])", str(t or "").strip()):
        if out and ABBR.search(out[-1]): out[-1] += " " + p   # "U.S. job postings" stays one sentence
        elif p: out.append(p)
    return out

def x_trim(summary, limit=240):
    """First sentence of X's summary, plus the second if both fit in ~limit characters."""
    ss = split_sentences(summary)
    if not ss: return ""
    out = ss[0]
    if len(ss) > 1 and len(out) + 1 + len(ss[1]) <= limit: out += " " + ss[1]
    return out

def x_segments(xdata, part, count=3):
    xs = [h for h in (xdata or {}).get("stories") or [] if h and h.get("title")][:count]
    if not xs: return []
    when = X_WHEN.get(part_of_day(part), "")
    segs = [{"hl": -1, "text": f"And from X{' ' + when if when else ''}, {'a story' if len(xs) == 1 else 'a few stories'} people are talking about."}]
    leads = ["", "Also on X. ", "One more from X. "]
    for j, h in enumerate(xs):
        segs.append({"hl": -1, "x": j, "text": f"{leads[min(j, 2)]}{sentence(clean_title(h['title']))} {sentence(x_trim(h.get('summary')))}".strip()})
    return segs

def x_fingerprint(xdata, count=3):
    return [h.get("x_id") or h["title"] for h in ((xdata or {}).get("stories") or []) if h and h.get("title")][:count]

def fingerprint(data):
    """hn_id (or title) of each headline the briefing reads, in order. Mirrors podcastFingerprint() in index.html."""
    hs = [h for h in (data.get("headlines") or []) if h and h.get("title")][:8]
    return [h.get("hn_id") or h["title"] for h in hs]

# ---- TTS-only text tweaks (spoken form; captions/cues unaffected) -------------
SAY_X = {  # extra spoken-form fixes, applied to the X segments only (the headline segments render exactly as before)
    "François Chollet": "Fran swah Shollay", "Chollet": "Shollay", "U.S.": "U S", "Inc.": "Incorporated", "T3": "T three",
}

SAY = {  # spoken-form fixes for words the default voice stumbles on (extend with --say file.json)
    "ipinfo": "I P info", "IPinfo": "I P info", "MiniMax": "Mini Max", "amdgpu": "A M D G P U", "OpenBSD": "Open B S D", "bsd": "B S D",
}

ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()

def words_lt100(n):
    return ONES[n] if n < 20 else TENS[n // 10] + ("" if n % 10 == 0 else "-" + ONES[n % 10])

def say_numbers(t):
    """X summaries keep digits; the default voice drops or garbles some ("20%", "2025"), so spell them out."""
    t = re.sub(r"\b(\d{1,2})%", lambda m: words_lt100(int(m.group(1))) + " percent", t)
    t = re.sub(r"\b(?:19|20)(\d\d)\b", lambda m: m.group(0)[:2].replace("19", "nineteen").replace("20", "twenty") + " "
               + ("hundred" if m.group(1) == "00" else "oh " + ONES[int(m.group(1))] if m.group(1)[0] == "0" else words_lt100(int(m.group(1)))), t)
    t = re.sub(r"\b(\d{1,2})(?=-)", lambda m: words_lt100(int(m.group(1))), t)   # 10-point -> ten-point
    return t

def speakable(text, say=SAY, x=False):
    t = text
    if x:
        for k, v in SAY_X.items(): t = t.replace(k, v)
        t = say_numbers(t)
    t = re.sub(r"\bShow HN\b:?", "Show H N:", t)
    t = re.sub(r"\bAsk HN\b:?", "Ask H N:", t)
    t = re.sub(r"\bHN\b", "H N", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"\bLofi\b", "Lo-fi", t)  # nudges "low-fye" instead of "loafy"
    for k, v in say.items(): t = re.sub(rf"\b{re.escape(k)}\b", v, t)
    t = re.sub(r"\b([a-z]+)drm\b", r"\1 D R M", t, flags=re.I)                  # inteldrm -> intel D R M
    t = re.sub(r"\bdot (io|ai|fm|ly|gg|tv)\b", lambda m: "dot " + " ".join(m.group(1).upper()), t)
    t = re.sub(r"(?<![\d.])(\d+)\.(\d+)(?!\d|\.\d)", r"\1 point \2", t)            # 6.18 -> 6 point 18
    def slash_list(m):  # a/b/c -> a, b, and c
        parts = m.group(0).split("/"); return ", ".join(parts[:-1]) + (", and " if len(parts) > 2 else " and ") + parts[-1]
    t = re.sub(r"\b[\w ]+?(?:/[\w ]+?)+\b(?= drivers| support|[,.]| from)", slash_list, t) if "/" in t else t
    t = re.sub(r"(?<=\w)/(?=\w)", " slash ", t)
    t = t.replace("&", " and ").replace("—", ", ").replace("–", ", ")
    return re.sub(r"\s+", " ", t).strip()

def chunks(text, limit=220):
    """Split into sentence groups of <= ~limit chars; Chatterbox gets unreliable on very long inputs."""
    out, cur = [], ""
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if cur and len(cur) + 1 + len(sent) > limit: out.append(cur); cur = sent
        else: cur = f"{cur} {sent}".strip()
    if cur: out.append(cur)
    return out

# ---- audio helpers ------------------------------------------------------------
def trim_silence(w, sr, thresh_db=-45.0, pad_s=0.06):
    import numpy as np
    if w.size == 0: return w
    frame = int(sr * 0.01)
    n = len(w) // frame
    if n == 0: return w
    rms = np.sqrt((w[:n * frame].reshape(n, frame) ** 2).mean(1) + 1e-12)
    db = 20 * np.log10(rms + 1e-12)
    on = np.where(db > thresh_db)[0]
    if on.size == 0: return w
    a = max(0, on[0] * frame - int(pad_s * sr)); b = min(len(w), (on[-1] + 1) * frame + int(pad_s * sr))
    seg = w[a:b].copy()
    f = min(int(0.01 * sr), len(seg) // 2)  # 10 ms fades to avoid clicks
    if f > 0:
        seg[:f] *= np.linspace(0, 1, f); seg[-f:] *= np.linspace(1, 0, f)
    return seg

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("headlines")
    ap.add_argument("--out", default="host_briefing.mp3")
    ap.add_argument("--cues", default="podcast_cues.json", help="episode.json snippet output")
    ap.add_argument("--audio-path", default=None, help="podcast_audio value as the dashboard should fetch it (default: out filename)")
    ap.add_argument("--part", default="auto", choices=["auto", "none", "morning", "afternoon", "evening", "night"],
                    help="greeting baked into the audio; 'none' = time-neutral 'Welcome to ...' (good for demos)")
    ap.add_argument("--exaggeration", type=float, default=0.4)
    ap.add_argument("--cfg-weight", type=float, default=0.35)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--pause", type=float, default=0.65, help="seconds between segments (dashboard uses 650 ms)")
    ap.add_argument("--lead-in", type=float, default=0.3)
    ap.add_argument("--lufs", type=float, default=-16.0)
    ap.add_argument("--max-segments", type=int, default=0, help="render only the first N segments (for samples)")
    ap.add_argument("--merge-episode", default=None, help="also write podcast_audio/podcast_cues into this episode.json")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--say", default=None, help="JSON {word: spoken form} merged into the built-in SAY fixes")
    ap.add_argument("--x-news", default=None, help="x_news.json: read its top stories after the headlines")
    ap.add_argument("--x-count", type=int, default=3)
    ap.add_argument("--reseed", action="append", default=[], metavar="SEG:OFFSET",
                    help="re-roll one segment's take: add OFFSET to its seed (repeatable), e.g. --reseed 10:1")
    ap.add_argument("--print-text", action="store_true", help="print the briefing (and spoken form) and exit")
    a = ap.parse_args()

    data = json.loads(Path(a.headlines).read_text())
    say = dict(SAY, **(json.loads(Path(a.say).read_text()) if a.say else {}))
    segs = build_briefing(data, a.part)
    xdata = json.loads(Path(a.x_news).read_text()) if a.x_news else None
    xsegs = x_segments(xdata, a.part, a.x_count) if xdata else []
    if xsegs: segs = segs[:-1] + xsegs + segs[-1:]  # before the outro
    if a.max_segments: segs = segs[:a.max_segments]
    if a.print_text:
        for s in segs:
            print(f"[{s['hl']:>2}] {s['text']}")
            sp = speakable(s["text"], say, "x" in s)
            if sp != s["text"]: print(f"     say: {sp}")
        return

    import numpy as np, torch, pyloudnorm as pyln, soundfile as sf
    from chatterbox.tts import ChatterboxTTS
    dev = a.device if a.device != "auto" else ("cuda" if torch.cuda.is_available() else
                                              "mps" if torch.backends.mps.is_available() else "cpu")
    t0 = time.time(); model = ChatterboxTTS.from_pretrained(device=dev); sr = model.sr
    print(f"model loaded on {dev} in {time.time()-t0:.1f}s", file=sys.stderr)

    pieces, cues, cursor, segs_meta = [], [], 0.0, []
    lead = np.zeros(int(a.lead_in * sr), dtype=np.float32); pieces.append(lead); cursor += len(lead) / sr
    gap = np.zeros(int(a.pause * sr), dtype=np.float32)
    t_render = time.time()
    reseed = {int(k): int(v) for k, v in (r.split(":") for r in a.reseed)}
    for k, s in enumerate(segs):
        parts = []
        for j, chunk in enumerate(chunks(speakable(s["text"], say, "x" in s))):  # long segments (summaries) render per sentence group
            torch.manual_seed(a.seed + k + 100 * j + 1000 * reseed.get(k, 0))
            w = model.generate(chunk, exaggeration=a.exaggeration, cfg_weight=a.cfg_weight, temperature=a.temperature)
            if parts: parts.append(np.zeros(int(0.28 * sr), dtype=np.float32))
            parts.append(trim_silence(w.squeeze(0).cpu().numpy().astype(np.float32), sr))
        w = np.concatenate(parts)
        cue = {"t": round(cursor, 2), "headline": s["hl"]}
        if "x" in s: cue["x"] = s["x"]  # X story index in x_news.json "stories"
        cues.append(cue)
        segs_meta.append({"start": round(cursor, 3), "end": round(cursor + len(w) / sr, 3), **{k: v for k, v in cue.items() if k != "t"}, "text": s["text"]})
        print(f"  seg {k} hl={s['hl']:>2} {len(w)/sr:5.2f}s", file=sys.stderr)
        pieces.append(w); cursor += len(w) / sr
        if k < len(segs) - 1: pieces.append(gap); cursor += len(gap) / sr
    pieces.append(np.zeros(int(0.4 * sr), dtype=np.float32))
    audio = np.concatenate(pieces)
    render_s = time.time() - t_render

    meter = pyln.Meter(sr)
    audio = pyln.normalize.loudness(audio, meter.integrated_loudness(audio), a.lufs)
    peak = np.abs(audio).max(); ceiling = 10 ** (-1.0 / 20)
    if peak > ceiling: audio = audio * (ceiling / peak)  # keep true-ish peak <= -1 dBFS
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "x.wav"); sf.write(wav, audio, sr, subtype="PCM_16")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-ac", "1", "-ar", "24000",
                        "-c:a", "libmp3lame", "-b:a", "96k", str(out)], check=True)
    dur = len(audio) / sr
    snippet = {"podcast_audio": a.audio_path or out.name, "podcast_cues": cues,
               # fingerprint of the headlines this audio reads (dashboard falls back to the browser voice on mismatch)
               "podcast_headlines": fingerprint(data),
               **({"podcast_x": x_fingerprint(xdata, a.x_count)} if xsegs else {}),
               "podcast_meta": {"engine": "chatterbox-tts (default voice)", "duration_s": round(dur, 2),
                                "exaggeration": a.exaggeration, "cfg_weight": a.cfg_weight, "temperature": a.temperature,
                                "seed": a.seed, "part_of_day": part_of_day(a.part),
                                "rendered": datetime.now().astimezone().isoformat(timespec="seconds"),
                                "segments": segs_meta}}
    Path(a.cues).write_text(json.dumps(snippet, indent=2, ensure_ascii=False) + "\n")
    if a.merge_episode:
        p = Path(a.merge_episode); ep = json.loads(p.read_text()) if p.exists() else {}
        ep["podcast_audio"] = snippet["podcast_audio"]; ep["podcast_cues"] = cues
        ep["podcast_headlines"] = snippet["podcast_headlines"]
        if "podcast_x" in snippet: ep["podcast_x"] = snippet["podcast_x"]
        else: ep.pop("podcast_x", None)
        ep["podcast_voice"] = {k: snippet["podcast_meta"][k] for k in
                               ("engine", "duration_s", "exaggeration", "cfg_weight", "temperature", "seed", "part_of_day", "rendered")}
        p.write_text(json.dumps(ep, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {out} ({dur:.1f}s) and {a.cues}; render {render_s:.0f}s = {render_s/dur:.2f}s per audio second",
          file=sys.stderr)

if __name__ == "__main__":
    main()
