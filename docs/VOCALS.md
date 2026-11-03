# Optional track vocals

Vocals are off until you ask. `./run.sh` does not install them. A synthesize call with no `vocals` field, or with `vocals` set to `none`, is the instrumental pipeline, same as before.

The dashboard has a Vocals control (None / Host summary / Song) shared by paste-a-link and Learn a topic. It defaults to None. While Chatterbox is rendering, the stage line says "Rendering vocals…". If the voice engine is missing, the beat is unchanged and `vocals.state` is `instrumental` with `vocals.reason` explaining why. The dashboard shows that reason.

Chatterbox is the optional extra already used for the 101.5 host briefing. Track vocals reuse `scripts/requirements-voice.txt`. There is no second requirements file.

## Ask for them

`vocals` is `none` (default), `summary`, or `song`.

- `summary`: up to four short DJ lines from the title, topics, and mood.
- `song`: four short original spoken lines from the topics. Chatterbox speaks. It does not sing.
- `script` or `lyrics`: your words. They win over the generated lines. Longer than 700 characters is clipped. No LLM and no API key.

```bash
curl -s -X POST http://127.0.0.1:53343/api/synthesize \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","vocals":"summary"}'
```

The same fields exist on `POST /api/synthesize_topic` and on the MCP tools `synthesize_url` and `synthesize_topic`. Leave them off and those tools behave as they do today.

When a render finishes, the response `track` is a mixed MP3 (vocal about 2 dB over the beat, beat ducked about 3 dB) plus `vocals.vocal_stem_url`. The stem is not a separate row in the track picker. Playback of the mix uses the existing player. The Voice slider is unchanged.

There is no line-by-line live layering and no extra demo MP3 in the repo.

## Install (opt-in)

Python 3.11, in a separate venv so the default `./.venv` stays as it is. One step:

```bash
./run.sh --voice
```

That creates `.venv-voice` with `python3.11`, installs `torch==2.6.0` and `torchaudio==2.6.0`, then `scripts/requirements-voice.txt` and `requirements.txt`, and starts the server with that interpreter. It sets `CHATTERBOX_DEVICE=mps` on Apple Silicon and `cpu` otherwise. If `python3.11` is not on `PATH`, it exits and tells you to install it. The first model load takes several minutes. A warm 4-line summary takes about 30 seconds on an M1 Max.

The same install by hand, from the repo root:

```bash
python3.11 -m venv .venv-voice
. .venv-voice/bin/activate
pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cpu
pip install -r scripts/requirements-voice.txt
pip install -r requirements.txt
CHATTERBOX_DEVICE=cpu PY=.venv-voice/bin/python ./run.sh
```

On an Apple Silicon Mac, install torch from PyPI (no CPU index) and set `CHATTERBOX_DEVICE=mps`. The built-in Chatterbox voice is the only voice. Nothing is cloned, and Resemble's Perth watermark stays on. Model weights stay in the Hugging Face cache. They are not committed.

Settings for track vocals: exaggeration/cfg `0.5/0.5` for a calm or analytical read, `0.7/0.3` for an energetic read or a song that is not calm, temperature `0.7`, ffmpeg atempo `0.9`, then the existing lofi chain in `audio/lofi_vocals.py`. The host briefing script is unchanged and keeps its own calmer settings.
