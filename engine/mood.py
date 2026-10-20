"""engine/mood.py - deterministic text -> lofi mood analysis (no LLM).

Keyword lexicons + VADER sentiment (if installed) + text density -> PRD JSON:
{url, page_summary, sentiment, lofi_prompt,
 musical_attributes:{bpm, key, primary_instruments[], mood_tags[]}, cover_art_prompt}
"""
import hashlib, re
from collections import Counter

LEX = {
    "analytical": """data analysis analyze model models api apis research algorithm documentation docs code function
        benchmark benchmarks tokens parameters framework sdk server servers install configure configuration performance
        architecture study report metrics library python javascript typescript developer developers engineering
        reference endpoint endpoints schema protocol repository repo github version release compute inference
        training dataset method results evaluation latency context pricing integration tool tools build cli""",
    "upbeat": """launch launches launched new celebrate celebrates win wins won record growth exciting excited amazing
        love success successful best free fun award breakthrough happy joy thrilled incredible awesome boost
        improve improved improvement opportunity bright welcome easy delight inspire inspiring open""",
    "dark": """war wars killed kill death deaths dead crisis attack attacks lawsuit sued fraud collapse layoffs shooting
        disaster threat threats conflict investigation charges charged victims inflation recession storm hurricane
        flood fire explosion arrested court prison violence protest sanctions warning risk risks danger dangerous
        decline crash outage breach hack hacked scandal fear fears injured strike missile troops ban bans banned
        tension tensions security restrictions restricted concerns concern espionage spy spying surveillance accused
        allegations alleged dispute tariffs tariff rivalry crackdown military homeland removed warns warned""",
    "calm": """nature garden quiet peace peaceful slow coffee tea rain home travel ocean sea forest meditation sleep
        gentle cozy calm relax relaxing walk morning sunset weekend book books reading recipe cook cooking simple""",
    "melancholic": """memory memories remember loss lost miss missing old history past ago farewell lonely goodbye
        retire retired nostalgia nostalgic childhood mourning grief tribute legacy remembered final last ending""",
    "energetic": """game games sport sports match race speed fast faster team score championship hype festival dance
        party concert tour league playoff playoffs final finals goal goals rally rush power powerful turbo""",
}
LEX = {k: set(v.split()) for k, v in LEX.items()}
# reporting-language markers: news articles use tech words without being documentation
NEWS = set("""said says told reported reporting according published spokesperson statement officials official
    announced department minister ministry agency agencies court filing lawmakers senator president""".split())

PROFILE = {  # base bpm, candidate keys, instruments, descriptor tags, scene, palette
    "analytical": (74, ["A minor", "D minor", "E minor"], ["Rhodes", "felt piano", "upright bass", "dusty drums", "vinyl crackle"],
                   ["focused", "late-night study"], "a desk with a glowing laptop, notebooks and a mug of coffee at night", "deep blue and amber"),
    "upbeat": (84, ["C major", "F major", "G major", "D major"], ["Rhodes", "nylon guitar", "vibraphone", "sub bass", "dusty drums"],
               ["warm", "sunny"], "a sunlit window with plants and a record player on a bright afternoon", "peach, mint and cream"),
    "dark": (66, ["C minor", "F minor", "Bb minor"], ["warm pad", "felt piano", "sub bass", "dusty drums", "vinyl crackle"],
             ["moody", "rain-soaked"], "a rainy city street under a single streetlight, seen through a fogged window", "charcoal, deep plum and cold teal"),
    "calm": (70, ["F major", "Eb major", "Ab major"], ["felt piano", "nylon guitar", "upright bass", "dusty drums", "rain"],
             ["calm", "cozy"], "a quiet bedroom with a cat asleep on a windowsill and soft lamp light", "soft lavender and warm beige"),
    "melancholic": (68, ["E minor", "D minor", "G minor"], ["felt piano", "warm pad", "upright bass", "dusty drums", "vinyl crackle"],
                    ["nostalgic", "wistful"], "an old train station at dusk with faded posters and falling leaves", "sepia, rust and dusk pink"),
    "energetic": (88, ["G minor", "D major", "A major"], ["Rhodes", "vibraphone", "sub bass", "dusty drums"],
                  ["bouncy", "head-nodding"], "a rooftop at golden hour with sneakers, headphones and city lights", "orange, magenta and electric blue"),
}

TEXTURES = ("vinyl crackle", "rain")
STOP = set("""the a an and or but if of to in on at by for with from as is are was were be been being it its this that these those
    you your we our they their he she his her them i me my not no yes do does did done can could will would should may might
    have has had more most other some such than then there here what which who whom when where why how all any each also into
    about over after before under between through during out up down off just only very so too new one two use using used get
    like make made see via per etc vs www http https com html page pages read more sign log learn click home menu
    retrieved latest news around said says year years people time first world today according archived original
    including well many much even back still last week since while across based""".split())


def _sentences(text):
    text = re.sub(r"\s+", " ", text)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", text) if s.strip()]


def _proseish(s):
    letters = sum(c.isalpha() or c == " " for c in s)
    return len(s.split()) >= 6 and letters / max(1, len(s)) > 0.85 and not re.search(r"[{}=;<>$]|\(\)|//", s)


def summarize(text, max_chars=320, n=3):
    picked = []
    for line in text.split("\n"):
        for s in _sentences(line):
            if _proseish(s):
                if not s.endswith((".", "!", "?")):
                    s += "."
                picked.append(s)
            if len(picked) >= n or len(" ".join(picked)) >= max_chars:
                break
        if len(picked) >= n or len(" ".join(picked)) >= max_chars:
            break
    out = " ".join(picked)
    if len(out) > max_chars:
        out = out[:max_chars].rsplit(" ", 1)[0] + "…"
    return out


def _sentiment(sents):
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    except ImportError:
        return None
    an = SentimentIntensityAnalyzer()
    scores = [an.polarity_scores(s)["compound"] for s in sents[:300] if len(s.split()) >= 4]
    return round(sum(scores) / len(scores), 3) if scores else 0.0


def _fallback_sentiment(words):
    pos = sum(w in LEX["upbeat"] for w in words); neg = sum(w in LEX["dark"] for w in words)
    return round((pos - neg) / max(1, pos + neg), 3) if pos + neg else 0.0


def topics(title, text, k=5):
    words = re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{2,}", (title + " ") * 3 + text)
    c = Counter(w.lower().strip(".-") for w in words)
    return [w for w, _ in c.most_common(80) if w not in STOP and len(w) > 3 and not w.isdigit()][:k]


def analyze(url, title, text):
    words = [w.lower() for w in re.findall(r"[A-Za-z']+", text)]
    nw = max(1, len(words))
    sents = [s for line in text.split("\n") for s in _sentences(line)]
    comp = _sentiment(sents)
    sent_src = "vader"
    if comp is None:
        comp, sent_src = _fallback_sentiment(words), "lexicon"
    title_words = [w.lower() for w in re.findall(r"[A-Za-z']+", title)]
    scores = {m: (sum(w in lx for w in words) + 3 * sum(w in lx for w in title_words)) * 1000 / nw for m, lx in LEX.items()}
    news = sum(w in NEWS for w in words) * 1000 / nw
    if news >= 8:  # reads like a news report -> weight the "serious" register, damp the docs register
        scores["dark"] *= 1.6; scores["analytical"] *= 0.6
    scores["upbeat"] += max(0, comp) * 25
    scores["dark"] += max(0, -comp) * 40
    scores["calm"] += 2  # gentle tie-breaker toward chill
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    primary, p_score = ranked[0]
    secondary = ranked[1][0] if ranked[1][1] >= 0.5 * p_score else None

    base_bpm, keys, instruments, tags, scene, palette = PROFILE[primary]
    prose = [s for s in sents if _proseish(s)] or sents
    avg_len = sum(len(s.split()) for s in prose) / max(1, len(prose))
    bpm = base_bpm + (2 if avg_len < 12 else -2 if avg_len > 25 else 0) + round(comp * 3)
    if secondary in ("energetic", "upbeat"): bpm += 2
    if secondary in ("dark", "melancholic", "calm"): bpm -= 2
    bpm = max(60, min(90, bpm))
    h = int(hashlib.sha1((url or title).encode()).hexdigest(), 16)
    key = keys[0] if primary == "analytical" else keys[h % len(keys)]  # analytical -> A minor (PRD example)
    instruments = list(instruments)
    if secondary and secondary != primary:
        extra = [i for i in PROFILE[secondary][2] if i not in instruments and i not in TEXTURES + ("dusty drums",)]
        if extra: instruments.insert(2, extra[0])
    while len(instruments) > 5:  # drop textures first, never the drums
        drop = next((i for i in reversed(instruments) if i in TEXTURES), None) or \
               next(i for i in reversed(instruments) if i != "dusty drums")
        instruments.remove(drop)

    label = "positive" if comp >= 0.25 else "mildly positive" if comp >= 0.05 else \
            "negative" if comp <= -0.25 else "mildly negative" if comp <= -0.05 else "neutral"
    sentiment = f"{primary}, {label}"
    mood_tags = [primary] + ([secondary] if secondary else []) + tags
    mood_tags = list(dict.fromkeys(mood_tags))[:5]
    tps = topics(title, text)
    topic_str = ", ".join(tps[:4]) if tps else "the page"
    short_title = (title or url)[:90]
    inst_str = ", ".join(instruments[:-1]) + " and " + instruments[-1] if len(instruments) > 1 else instruments[0]
    lofi_prompt = (f"Lofi hip hop instrumental, {bpm} BPM in {key}, {' and '.join(mood_tags[:2])} mood, "
                   f"{', '.join(mood_tags[2:]) or 'chill'} vibe. {inst_str[0].upper() + inst_str[1:]}, swung boom-bap groove, tape wobble. "
                   f"Inspired by \"{short_title}\" (themes: {topic_str}).")
    cover_art_prompt = (f"Cozy lofi illustration of {scene}, with subtle visual nods to {topic_str}. "
                        f"{palette[0].upper() + palette[1:]} palette, soft grain, anime-inspired, square album cover, no text.")
    mood = {
        "url": url,
        "page_summary": summarize(text) or (title or url),
        "sentiment": sentiment,
        "lofi_prompt": lofi_prompt,
        "musical_attributes": {"bpm": bpm, "key": key, "primary_instruments": instruments, "mood_tags": mood_tags},
        "cover_art_prompt": cover_art_prompt,
    }
    debug = {"scores": {k: round(v, 2) for k, v in ranked}, "compound": comp, "sentiment_source": sent_src,
             "avg_sentence_words": round(avg_len, 1), "news_markers_per_1k": round(news, 1), "words": nw, "topics": tps}
    return mood, debug


if __name__ == "__main__":
    import json, os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from engine.ingest import ingest
    d = ingest(sys.argv[1]); m, dbg = analyze(d["url"], d["title"], d["text"])
    print(json.dumps(m, indent=2)); print(json.dumps(dbg), file=sys.stderr)
