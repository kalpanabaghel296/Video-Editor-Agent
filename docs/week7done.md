# Changelog — Week 7

## Week 7 — Smarter editing: best takes, face-aware framing, chat revise, reference style

### Added
- **Best-take selection** (`analysis/takes.py`, used by `analysis/fumbles.py`). Finds *groups* of takes (same line said 2+ times, also when other sentences are in between, or when a take was abandoned half-way), scores each take on fluency (fillers, stutters, hesitations), completeness, pace, audio level and a small "later take" bonus, keeps the **best** and cuts the rest. Old rule: always drop the earlier one. `GET /api/jobs/{id}/takes` and an "Repeated takes found" box in the UI show the scores.
- **Face-aware reframing** (`editing/reframe.py`). When a wide video becomes 9:16 / 1:1 the crop window follows the speaker (OpenCV Haar cascade, ~4 samples/s, virtual-tripod smoothing with a dead zone and a speed limit, path simplified to a short ffmpeg `crop` expression of time). The path is stored in the EDL (`reframes`). No face found → the old centre crop. `blurred background` (`fit: "blur"`) added as an alternative to black bars.
- **Chat revise** (`agents/revise.py`, `POST /api/jobs/{id}/chat`). Follow-up messages ("zoom kam karo", "caption chhote karo", "20 second me", "thoda aur chhota") are merged into the previous instruction group by group (duration, zoom, captions, music, sfx, noise, look, format, framing, cuts, title), so old conflicting words can never win. Chat card in the UI with history.
- **Reference style matching** (`analysis/reference.py`, `/api/jobs/{id}/reference`). Measures a reference video (aspect, length, average shot length via histogram/pixel differences, brightness / contrast / saturation / warmth, loudness) and applies pace, format, look (relative to your own footage) and optionally length. The prompt always wins.
- **Platform presets** (`agents/presets.py`, `GET /api/presets`): Instagram Reel, YouTube Short, TikTok, LinkedIn, YouTube → size, caption look, caption safe zone, a sensible length when the source is far longer than the platform's recommended maximum.
- **Caption looks** (`editing/captions.py`): classic, hormozi, minimal, boxed, karaoke; sizes small / normal / large; platform safe zone; Devanagari text automatically uses *Nirmala UI*. Chosen in the prompt ("hormozi captions") or in the Captions tab (the UI choice wins).
- **Hinglish prompts**: chhota, hata do, galtiyan, ruk-ruk ke, laga do, bina, tez, ... (`director.normalize_text`); negations in both orders ("no zoom" / "zoom hata do"); `zoom_strength` (subtle / normal / strong).
- **FFmpeg detection** (`utils/ffmpeg.py`): `FFMPEG_PATH`, `PATH`, the Windows registry PATH and the winget / common install folders are searched, so a server started *before* FFmpeg was installed still works. `/api/health` reports `ffmpeg_ok`; the UI shows a banner with a Re-check button. Missing binary → `FFmpegMissing` with a clear install hint instead of `[WinError 2]`.
- **Upload safety**: `MAX_UPLOAD_MB`, `MAX_REFERENCE_MB`; the file is probed with ffprobe and rejected (and deleted) if it is not a real video.
- Friendly job errors (`utils/errors.py`): FFmpeg missing, Application Control, Whisper download problems. The raw text is kept in `error_raw`.
- Run scripts use `python -m pip` / `python -m uvicorn` and print an FFmpeg check.
- Tests: `tests/test_week7.py` (41 tests, parametrized cases included).

### Fixed
- The plain word **"short"** ("make it short, remove the fumbling") was read as *YouTube Shorts* and forced a 9:16 crop. Only "YouTube Short(s)", "yt shorts" and "shorts" select that platform now.
- Reference look sliders no longer blow up on a flat / very low-contrast frame (floor on both sides of the ratio).

### Known limits
- Face tracking uses OpenCV Haar cascades: good for a frontal speaker, weaker for profile views, masks and very low light. It falls back to a centre crop when fewer than 25 % of the sampled frames contain a face.
- Reference matching does not detect caption style, music vs speech or transitions; soft cuts between very similar shots (dissolves) can be missed.
- Chat revise understands the vocabulary in `agents/director.py` / `agents/revise.py`; unknown messages are appended to the instruction as extra text (the reply says so).
