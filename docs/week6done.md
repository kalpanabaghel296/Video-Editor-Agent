# Changelog

All notable changes to this project are listed here, newest first.
Format based on [Keep a Changelog](https://keepachangelog.com/).

## Week 6 — Quality, Brain and Studio UI

### Added
- **Look & Effects** (`editing/look.py`): 9 filters, brightness / contrast / saturation / warmth / sharpen / vignette / film grain sliders, fade in/out, cinema bars, mirror. Can be set from the UI or the prompt. Applied before captions so captions keep their colours.
- **Agent brain** (`analysis/fumbles.py`): detects long pauses (including pauses hidden by background noise), hesitations, stutters, false starts and re-takes; every cut has a reason and a timestamp.
- **Spectral noise removal** (`editing/denoise.py`): audio only, same length as the source. `normal` and `strong` levels.
- **Background music** (`editing/music.py`): built-in calm / neutral / upbeat track, or upload your own; automatic ducking while someone speaks.
- **Studio UI** (`frontend/index.html`): projects list, Original / Edited / Side-by-side, visual timeline (clips, captions, SFX, zoom, fades), "What the agent did" list, EDL JSON viewer, caption editor, dark and light theme, mobile layout.
- **Sound check**: live level meter and file-level audio check (`GET /api/jobs/{id}/audio-check`).
- Prompt understanding: caption script (`00:03 – 00:07: "text"`), time windows (`0:00–0:15`, `1:05 to 1:35`), "first N seconds", sound-effect, look and music-mood words.
- Sound effects (whoosh, pop, click, ding) on cuts and caption starts.
- API: `PUT/GET /api/jobs/{id}/look`, `POST/DELETE /api/jobs/{id}/music`, `PUT /api/jobs/{id}/transcript`, `retranscribe` flag on `/edit`, `look` and `music` fields on job creation.
- Tests: 20 new tests (`test_fixes.py`, `test_look.py`); total 48.

### Changed
- **Exact length**: the planner now fills the remaining time with neighbouring content, so a 10 s request gives about 10 s (was 8.79 s). The last cut lands on the end of a word.
- Two selection modes: `head` ("trim to N s") and `best` ("make an N s reel").
- **Preserve mode**: prompts that only ask for noise removal, music, captions or a look keep every frame.
- Loudness: quiet recordings are lifted towards -16 LUFS with a measured, capped gain.
- Whisper defaults: model `small`, beam search, Hinglish prompt, optional language hint.
- Videos are served with HTTP Range support.

### Fixed
- Backend crashed on start (missing `Tuple` / `Optional` imports).
- Prompt ignored: the old parser could not read `00:00:00 to 00:00:15` and ignored caption scripts.
- "Trim to 10 seconds" returned 8.79 s.
- "Remove background noise" deleted frames (31.4 s became 24.5 s).
- Noise removal reduced pause noise by only about 3 dB (now about 20 dB on `normal`, 40 dB on `strong` in a synthetic test).
- Background music was mixed too quietly to be heard.
- A plain loudness normaliser pumped residual noise back up; replaced by the capped gain.

### Measured results (synthetic and real-video tests)
| Check | Result |
|---|---|
| Trim 10 s | 9.94 s |
| Denoise speech quality (SDR) | 8 dB → 24 dB (`normal`), 26 dB (`strong`) |
| Noise in pauses | -30 dB → -50 dB (`normal`), -70 dB (`strong`) |
| Music vs speech | about 11 dB below overall, about 18 dB below while speaking |
| Remove-noise prompt | 31.4 s in, 31.4 s out |

## Week 5 — Polish and extra effects
- Punch-in zoom on alternate cuts, fades, title cards, colour grade, noise reduction option.
- Background music with ducking, styled (word-highlight) captions.
- Explainability: a readable explanation is returned with every EDL.
- Preview players, download button, re-run with a modified instruction.

## Week 4 — Validation and revise loop
- Validator (schema, timestamps, ranges) with automatic patches (clamp, drop, trim to target).
- Output inspector (file exists, playable, video and audio streams, duration, aspect ratio).
- Four render fallback levels and a retry limit (`MAX_RETRIES`).
- Job manager with per-stage status and log; progress tracker in the UI.

## Week 3 — Director, EDL and first render
- Director: instruction → requirements (rules, optional LLM).
- Planner: transcript segments → clips → EDL.
- Renderer: trim, concat, 9:16 / 1:1 / 16:9, burned-in captions.
- EDL preview in the UI.

## Week 2 — Analysis layer
- Transcript (faster-whisper, word timestamps), scene detection (PySceneDetect), silence detection (FFmpeg `silencedetect`), filler words.
- EDL schema (Pydantic) and analysis orchestrator.
- Analysis progress in the UI.

## Week 1 — Foundation
- Repository, FastAPI backend, upload endpoint, per-job folders, metadata extraction (ffprobe), basic frontend.