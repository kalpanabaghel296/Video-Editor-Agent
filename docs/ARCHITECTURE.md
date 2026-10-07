# Architecture

```
Upload ─▶ OBSERVE ─▶ UNDERSTAND ─▶ PLAN ─▶ ACT ─▶ INSPECT ─▶ (REVISE ↺) ─▶ EXPORT
          metadata     director    planner  renderer  inspector   patch +       final.mp4
          transcript   (NL→reqs)   (→EDL)  (FFmpeg)  (ffprobe)   re-render
          scenes                    ▲
          silence                validator (clamp / drop / trim)
          fillers
```

| Stage | Module | Output |
|---|---|---|
| OBSERVE | `analysis/pipeline.py` | `analysis/{metadata,transcript,scenes,silence,fillers}.json` |
| UNDERSTAND | `agents/director.py` | `Requirements` (duration, aspect, captions, music, style, keywords) |
| PLAN | `agents/planner.py` + `agents/validator.py` | `edl.json` |
| ACT | `editing/renderer.py`, `editing/captions.py` | `output/draft.mp4` |
| INSPECT | `agents/inspector.py` | list of issues |
| REVISE | `agents/loop.py` | patched EDL / lower render level, max `MAX_RETRIES` |
| EXPORT | `agents/loop.py` | `output/final.mp4`, explanation text |

## Safety rule
The planner / LLM only ever produces **EDL JSON**. FFmpeg commands are built by our own code
as argument lists (no shell). Anything the LLM says is validated by Pydantic + `validator.py` first.

## EDL schema (`models/edl.py`)
`version`, `source_media`, `project` (duration, aspect_ratio, width, height, fps, fit), `clips[]`
(id, source_start, source_end, label, score), `audio` (music, volume, ducking), `captions[]` (timeline times,
optional word timings), `caption_settings`, `effects[]` (punch_in), `transitions[]` (fade into clip),
`overlays[]` (title cards, rendered via libass with fade), `audio.denoise` (highpass + afftdn), `explanation[]`. See `sample_edl.json`.

## Render fallback ladder (`renderer.render(level)`)
0 everything → 1 hard cuts only → 2 plain captions, no music → 3 bare cuts.
Inside a level each feature also falls back on its own (styled→plain captions, music→none).

## Director modes
`LLM_PROVIDER=none` (default, rule-based) · `ollama` (local, strict JSON) · `anthropic` (needs `ANTHROPIC_API_KEY`).
Any LLM failure silently falls back to the rule parser.

## API
`POST /api/jobs` (file + optional instruction) · `POST /api/jobs/{id}/edit` · `GET /api/jobs/{id}` ·
`/analysis` · `/edl` · `/edl/preview` · `/video?kind=source|draft|final` · `/download` · `/log`
