# Radio and News Graph

The tuner starts on 88.1 LOFI. 101.5 NEWS TALK reads the headlines over the beat. The tab bar switches to the News Graph without reloading the page. `#graph` opens the graph directly.

## Sub-features

- `radio-default` shows 88.1 and a LOFI preset after the page script runs.
- `radio-talk` shows a NEWS TALK preset. The committed briefing is `dashboard/host_briefing.mp3`, named by `episode.json` as `podcast_audio`.
- `graph-hash` opens the News Graph for `/dashboard/#graph` and draws the headline svg.
- `graph-headlines` uses the titles in `dashboard/headlines.json` as the nodes.

## How to get to it (user POV)

- Open `/dashboard/`. The FM dial (`#rDial`, `#rFreq`) reads 88.1. Presets are buttons with `data-st="lofi"` and `data-st="talk"`.
- Choose the 101.5 preset to hear News Talk. Press the play button (`#playBtn`) after a reload. Browsers block audio until a click.
- Choose the News Graph tab (`#tabGraph`) or open `/dashboard/#graph`.

## Driving it with session.sh

Preconditions:

- Doctor has succeeded for this `$EVIDENCE`.
- `capture-control-room` has already written `dashboard.dom.html` and `graph.dom.html`, or Chrome is on `PATH` so this journey can run that capture.
- Switching stations by click needs a browser driver that can click `[data-st="talk"]`. This repo does not ship one. Rendered presets are the proof that the control exists. A click that changes `#rFreq` to `101.5` is `verified-unreachable` until that driver exists. Record the attempted selector and the missing driver. Do not claim the station changed because the preset button was present.

- **Default station in the DOM.** Open `$EVIDENCE/proof/dashboard.dom.html`. It contains `data-st="lofi"`, `data-st="talk"`, and `id="rFreq"`. The static HTML before scripts run already contains `id="rFreq">88.1`.
- **Briefing file.** Run `curl -s -o /dev/null -D "$EVIDENCE/proof/host-briefing.headers" -w '%{http_code} %{size_download}\n' "$BASE/dashboard/host_briefing.mp3"`. Status `200` and the body is larger than 1000 bytes. `episode.json` from the control-room capture has `"podcast_audio": "host_briefing.mp3"`.
- **Graph URL.** Open `$EVIDENCE/proof/graph.dom.html` from a dump of `$BASE/dashboard/#graph`. `id="viewGraph"` is present and that element is not `hidden`. `id="gSvg"` is present. If the capture wrote `browser-skipped.txt`, this entry is not verified.
- **Node text.** The graph DOM includes the title of the first headline in `dashboard/headlines.json`. If it does not, record the title you looked for and treat the draw as failed.

## Gotchas

- The 101.5 preset is created by script, not by the static HTML. A curl of `/dashboard/` cannot see `data-st`. Use the DOM dump.
- While `episode.json` `podcast_headlines` matches `headlines.json`, the page stays on the demo snapshot and the ticker says the snapshot is in use. `?hn=1` forces a live Hacker News refresh. Leave that off during this journey so the briefing fingerprint still matches.
- `?x=0` hides X stories from the ticker and the graph. Leave it off unless the journey is about that flag.
- Audio does not start in headless Chrome. A screenshot of the paused player is the expected state. The play button begins disabled until a track is attached.
