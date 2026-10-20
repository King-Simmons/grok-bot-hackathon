# Optional track vocals

Vocals are off. `./run.sh` does not install them, and the dashboard does not ask for them. A synthesize call with no `vocals` field is the instrumental pipeline, same as before.

Chatterbox is the optional extra already used for the 101.5 host briefing. Track vocals reuse `scripts/requirements-voice.txt`. There is no second requirements file. If that package is not installed, a request for vocals is ignored and the instrumental result is returned unchanged.

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

Python 3.11. From the repo root, in a separate venv so the default `./.venv` stays as it is:

```bash
python3.11 -m venv .venv-voice
. .venv-voice/bin/activate
pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cpu
pip install -r scripts/requirements-voice.txt
pip install 'setuptools<81'
pip install 'certifi>=2024.2' 'scipy>=1.13' 'pyloudnorm>=0.1.1' 'lameenc>=1.7' 'trafilatura>=2.0' 'vaderSentiment>=3.3' 'mcp>=2.3,<3'
```

Then run the server with that interpreter:

```bash
PY=.venv-voice/bin/python ./run.sh
```

`CHATTERBOX_DEVICE=cpu` is the default here. On an Apple Silicon Mac, `CHATTERBOX_DEVICE=mps` selects the GPU when PyTorch has it. The built-in Chatterbox voice is the only voice. Nothing is cloned, and Resemble's Perth watermark stays on. Model weights stay in the Hugging Face cache. They are not committed.

Settings for track vocals: exaggeration/cfg `0.5/0.5` for a calm or analytical read, `0.7/0.3` for an energetic read or a song that is not calm, temperature `0.7`, ffmpeg atempo `0.9`, then the existing lofi chain in `audio/lofi_vocals.py`. The host briefing script is unchanged and keeps its own calmer settings.
