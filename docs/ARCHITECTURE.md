# Architecture

```text
Upload ─▶ OBSERVE ─▶ UNDERSTAND ─▶ PLAN ─▶ ACT ─▶ INSPECT ─▶ (REVISE ↺) ─▶ EXPORT
          metadata     Director    Planner  Renderer  Inspector   patch +      final.mp4
          transcript   (prompt →    (→ EDL)  (FFmpeg)  (ffprobe)  re-render    + captions
          scenes        requirements)  ▲
          silence                 Validator (clamp / drop / trim)
          fillers
```

| Stage | Module | Output |
|---|---|---|
| OBSERVE | `analysis/pipeline.py` | `analysis/{metadata,transcript,scenes,silence,fillers}.json` (cached) |
| BRAIN | `analysis/fumbles.py` | list of cuts with reasons (computed on load, never cached) |
| UNDERSTAND | `agents/director.py` | `Requirements` (length / window, aspect, captions, music, SFX, look, noise, mode) |
| PLAN | `agents/planner.py` + `agents/validator.py` | `edl.json` |
| ACT | `editing/renderer.py` (+ `captions.py`, `denoise.py`, `music.py`, `look.py`) | `output/draft.mp4` |
| INSPECT | `agents/inspector.py` | list of issues |
| REVISE | `agents/loop.py` | patched EDL or lower render level, at most `MAX_RETRIES` |
| EXPORT | `agents/loop.py` | `output/final.mp4`, `.srt`, `.vtt`, explanation text |

## Safety rule
The planner (and the optional LLM) only ever produces **EDL JSON**. FFmpeg commands are built by our own code as argument lists (no shell). Anything an LLM says is validated by Pydantic and `validator.py` first. Exact times and dictated caption scripts always come from the rule parser, never from the LLM.

## Job folder
```text
jobs/<id>/
├── source/input.mp4
├── analysis/      metadata, transcript, scenes, silence, fillers, source captions
├── assets/        music.<ext>, look.json   (set from the UI)
├── work/          temporary render files
├── output/        draft.mp4, final.mp4, captions
├── edl.json   job.json   job.log
```

## Director and Planner
- **Director** (`LLM_PROVIDER=none` default): rules read the length, time window, aspect ratio, captions, zoom, SFX, noise, music (and mood), look words, and the dictated caption script. `ollama` and `anthropic` are optional; on any LLM failure the rule parser is used.
- **Edit intent**: the prompt is classified. Prompts that only ask for enhancement (noise, music, captions, look) switch on **preserve mode**: nothing is cut. An exact window (`0:00–0:15`) also disables automatic cuts.
- **Selection modes**: `head` (first N seconds of the cleaned video) or `best` (highest-scoring segments, then filled to the target with the neighbouring content). The last cut is snapped to the end of a word.
- **Agent brain** (`fumbles.py`): `long_pause`, `hesitation`, `stutter`, `false_start`, `retake`, `filler`. A cut pause keeps about 0.3 s of natural breath.

## EDL schema (`models/edl.py`)
`version`, `source_media`, `project` (duration, aspect_ratio, width, height, fps, fit, **look**), `clips[]` (id, source_start, source_end, label, score), `captions[]` (timeline times, optional word timings), `caption_settings`, `effects[]` (punch_in), `transitions[]` (fade into clip), `overlays[]` (title cards), `audio` (music, music_style, music_volume, music_path, ducking, denoise, denoise_level, **sfx, sfx_events[]**), `explanation[]`. See `sample_edl.json`.

`look`: `preset`, `brightness`, `contrast`, `saturation`, `warmth`, `sharpen`, `vignette`, `grain`, `fade_in_out`, `letterbox`, `mirror`.

## Renderer (`editing/renderer.py`)
Three passes, each with its own fallback:

1. **Base**: trim and concatenate clips, aspect-ratio fit (crop / pad), punch-in zoom, fades, per-clip noise handling, measured loudness gain.
2. **Look**: filters, colour and effects (`look.py`) over the cut video.
3. **Post**: burn-in captions and title cards, background music with ducking, then sound effects.

Noise removal (`denoise.py`) runs first on the extracted audio (spectral gating with a learned noise profile) and is fed into pass 1, so the timeline is unchanged. If it fails, an FFmpeg filter chain is used; if that fails, the video is exported without noise removal.

### Render fallback ladder (`render(level)`)
`0` everything → `1` hard cuts only (no effects / transitions) → `2` plain captions, no music → `3` bare cuts.
Inside a level each feature also falls back on its own (styled → plain captions, music → none, look → none).

## Validation
- **Validator** (before render): schema, clips exist, start ≥ 0, start < end, end ≤ source duration, clip order / overlap, effect and transition references, caption and overlay ranges, music file present, over-target length, even output resolution. Fixable issues are patched (clamp, drop, trim).
- **Inspector** (after render): file exists and plays, video and audio streams present, duration matches, aspect ratio matches, not over target.
- **Loop**: on a problem the broken part is patched and rendered again, at a lower fallback level if needed, up to `MAX_RETRIES`; then the job is marked `failed` with the reason.

## Frontend (`frontend/index.html`)
Single static page served by FastAPI (no build step). Projects list, video stage (Original / Edited / Side by side), visual timeline, decisions list, EDL viewer, and four tabs: Prompt, Look, Audio, Captions. Polls `/api/jobs/{id}` while a job runs. The live look preview uses CSS and is approximate.

## API
See the table in the [README](../README.md#-api).