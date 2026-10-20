# Voice engine (Chatterbox TTS)

Spoken clips for Lofi News Radio use [Chatterbox](https://github.com/resemble-ai/chatterbox), Resemble AI's MIT-licensed TTS (`pip` package `chatterbox-tts`). The stable entry point is one function. Summary mode, song mode, lyric or script generation, beat alignment, ducking, and a per-track voice stem should call it and do that work themselves.

```python
from audio.voice import synthesize_speech, PRESETS, VoiceUnavailable

news = PRESETS["news"]  # exaggeration 0.7, cfg_weight 0.3, speed 0.9
try:
    clip = synthesize_speech(text, news["exaggeration"], news["cfg_weight"], news["speed"], out_path)
except VoiceUnavailable:
    clip = None  # chatterbox is optional; the beat and the browser voice still work
```

`synthesize_speech(text, exaggeration, cfg_weight, speed, out_path)` returns a dict. These keys stay:

| key | meaning |
|---|---|
| `path` | absolute path of the written audio |
| `file` | repo-relative path when the file is inside the repo, otherwise `path` |
| `url` | site path such as `/tracks/speech-….wav`, or `null` if the file is outside the repo |
| `duration_s` | length of the file |
| `sample_rate` | rate of the file (Chatterbox's rate, or 44100 after `lofi=True`) |
| `exaggeration`, `cfg_weight`, `speed` | the controls that were used |
| `lofi` | whether the vocal chain ran |
| `cached` | true when `out_path` was omitted and an identical clip was already on disk |
| `voice` | always `chatterbox-default` |
| `watermark` | always `perth` |
| `load_s`, `synth_s` | model-load time and synthesis time; both 0 on a cache hit |

`lofi` is keyword-only and defaults to false. It is not part of the positional contract.

```python
synthesize_speech(text, exaggeration, cfg_weight, speed, out_path, lofi=False)
```

## Controls

Chatterbox's own defaults are exaggeration `0.5`, cfg_weight `0.5`, and speed `1` (no time-stretch). A CPU listening pass found two useful reads. Pass them yourself; the function does not take a style argument.

| read | exaggeration | cfg_weight | speed |
|---|---|---|---|
| News anchor (`PRESETS["news"]`) | 0.7 | 0.3 | 0.9 |
| DJ (`PRESETS["dj"]`) | 0.5 | 0.5 | 0.9 |

`speed` is ffmpeg `atempo` after synthesis. It changes duration, not pitch. `1` skips ffmpeg. The built-in voice is a little fast for lofi, which is why the presets use `0.9`. Allowed range is 0.5 to 2.

`resolve_controls(exaggeration, cfg_weight, speed, preset)` fills anything you omit. Explicit numbers win over `news` or `dj`. The HTTP and MCP wrappers use it. Call `synthesize_speech` directly if you already have the three numbers.

For a dry stem (alignment and ducking downstream), leave `lofi` off and `speed` at `1`, and pass your own `out_path`. `lofi=True` reuses `lofi_chain` in `audio/lofi_vocals.py`: high-pass around 250 Hz, low-pass around 3.5 kHz, light saturation, a short reverb.

Long text is split around 280 characters and joined with a short gap, because one Chatterbox call will not finish an arbitrarily long read. Above 8000 characters the function raises `ValueError`. One call per line keeps the timing yours.

When `out_path` is set, that file is overwritten. When it is omitted, the clip is stored at `tracks/speech-<hash>.wav` (gitignored) and a later call with the same text and controls returns it. Generation is stochastic; the cache is what keeps a stem from changing under a second call.

## Install

`./run.sh` does not install this. Torch is large, and the rest of the app must run without it.

Tested: **Python 3.11**, `chatterbox-tts` 0.1.7, **CPU torch 2.6.0**. chatterbox-tts 0.1.7 does not support Python 3.13.

`resemble-perth` (imported while the model loads) fails on **setuptools 81 and newer**, and Chatterbox then crashes on load. Pin setuptools below 81 after the other packages so a dependency cannot leave a newer one in the venv:

```bash
python3.11 -m venv .venv-voice
.venv-voice/bin/pip install -U pip
.venv-voice/bin/pip install -r requirements.txt -r requirements-voice.txt
.venv-voice/bin/pip install 'setuptools>=61,<81'
PY=.venv-voice/bin/python ./run.sh
```

The first `from_pretrained` downloads the checkpoint into the Hugging Face cache (including `conds.pt`, the built-in voice). Those weights are not committed. `CHATTERBOX_DEVICE` overrides the device; the default is `cpu`.

If the package is missing, `synthesize_speech` raises `VoiceUnavailable`. `POST /api/synthesize` still renders music. News Talk still uses the browser voice, or a file you already rendered.

## CPU, so pre-generate

On CPU, after the model is warm:

- about **8 seconds** to load the model the first time
- about **2 seconds of compute per 1 second of audio**

Do not synthesize on the live tuner. Render clips ahead of time and point the station at the file. The control room already plays `dashboard/episode.json` → `podcast_audio` (and optional `podcast_cues`) through the Voice slider.

```bash
python3 -m audio.voice --text "Good evening. You're listening to News Talk." --preset news --out tracks/host-line.wav
python3 -m audio.voice --text-file host.txt --preset news --speed 0.9 --out tracks/host.wav
```

`--text-file` is one clip, not a timed script. Write the script in your own mode, then call `synthesize_speech` per line if you need cue points.

`GET /api/health` includes `"chatterbox": true|false`.

```
POST /api/speak
{"text": "...", "preset": "news", "speed": 0.9, "lofi": false, "out_path": "tracks/host-line.wav"}
{"text": "...", "async": true}
```

`preset` (alias `style`) is `news` or `dj`. Omitted controls use the Chatterbox defaults. `503` means the package is not installed; the body says how to add it. Poll async jobs at `GET /api/jobs/<id>`, same as `/api/synthesize`.

MCP tool `speak_text` is the same wrapper. It returns the clip dict, or `{"error": "...", "chatterbox": false}` when the package is missing, so a bot can continue. It does not change `synthesize_url`.

## Watermark

Every Chatterbox `generate` call embeds Resemble's [PerTh](https://github.com/resemble-ai/perth) watermark. It is inaudible and is meant to survive MP3 compression and light editing. This project does not strip it. `watermark` is `perth` on every clip.

## Do not clone a real person without consent

Chatterbox can clone a voice from a reference recording. This module never accepts one (`audio_prompt_path` is not a parameter). It uses only the built-in default voice shipped with the checkpoint. Do not add a cloning path, and do not clone a real person's voice without their consent.
