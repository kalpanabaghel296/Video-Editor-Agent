# AI Video Editing Agent

Upload a video, type a prompt ("30 second reel with captions and music, focus on pricing"),
get the edited video back. Agent loop: **Observe → Understand → Plan → Act → Inspect → Revise → Export**.

## Run
Requirements: Python 3.10+, **FFmpeg + ffprobe** on PATH.
```bash
./run.sh            # creates venv, installs deps, starts http://localhost:8000
```
Open http://localhost:8000 (frontend is served by FastAPI).

Transcription uses `faster-whisper` (model downloads on first run, `WHISPER_MODEL=base|small`).
Without it the agent still works (plans from silence/speech regions only, no captions text).

## Env vars
`WHISPER_MODEL` · `TRANSCRIBER=auto|faster-whisper|whisper|none` · `LLM_PROVIDER=none|ollama|anthropic` ·
`OLLAMA_MODEL` · `ANTHROPIC_API_KEY` · `MAX_RETRIES` · `SILENCE_DB` · `JOBS_DIR` · `MUSIC_DIR` (drop your own mp3 in `assets/music/`)

## Test
```bash
cd backend && pip install -r requirements.txt && pytest -q     # 21 tests, synthetic video, no Whisper needed
```

## Prompt examples
- `Make a 45 second reel with captions and music`
- `YouTube version, 1 minute, remove silence and fillers`
- `Square 1:1 clip, fast and energetic, zoom, focus on pricing`
- `Vertical 9:16 with fade transitions, no captions`
- `30 second reel, remove background noise, add music, title: "Pricing Secrets"`

## Team split (from the 5-week plan)
Member 1: models/EDL, director, planner, validator, job manager · Member 2: analysis, renderer, captions, effects ·
Member 3: frontend (upload, progress tracker, EDL preview, player, re-run).

See `docs/ARCHITECTURE.md` and `docs/sample_edl.json`.
