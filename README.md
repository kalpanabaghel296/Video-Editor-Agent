# Video Editor Agent

AI-powered autonomous video editing agent.

## Current Status (Weeks 1 to 5: Fully Functional Autonomous AI Video Editor)

The autonomous video editor is fully functional with single-pass rendering, platform presets, and conversational revisions:
- **Week 1 (Ingestion & Foundations)**: Multipart upload, UUID job isolation, asynchronous FFprobe technical metadata parsing, and job state machine.
- **Week 2 (Media Understanding & EDL)**: Local OpenAI Whisper STT, FFmpeg `silencedetect` pause detection, PySceneDetect fallback, and AI Director Edit Decision List (EDL) planning.
- **Week 3 (Precision Cutting & Subtitles)**: Frame-accurate single-pass FFmpeg cutting & concatenation, re-timed subtitle engine (`.srt`), burn-in option, and automated post-render Quality Validator.
- **Week 4 (Social Reframing & Audio Design)**: 9:16 vertical (Shorts/Reels) and 1:1 square canvas reframing with Gaussian-blurred background fill, background music mixing, and automated sidechain speech ducking.
- **Week 5 (Autonomous Orchestration & Revision)**: One-click autonomous editing (`POST /jobs/{job_id}/auto-edit`), built-in platform presets, 1.15x visual punch-in zoom, conversational revision feedback loop (`POST /jobs/{job_id}/revise`), and whole-timeline context-preserving narrative selection.

## Prerequisites

- **Python 3.10+** (tested on Python 3.14.5)
- **Node.js 18+** and **npm**
- **FFmpeg & FFprobe**: Must be installed and available in system/user `PATH`. Verify with `ffmpeg -version` and `ffprobe -version`.

## Quick Start

### 1. Backend Setup & Run

```bash
cd backend
python -m venv .venv
# On Windows: .venv\Scripts\activate
# On macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# Run the FastAPI server (runs on port 8000)
uvicorn app.main:app --reload
```

Interactive API documentation will be available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

### 2. Frontend Setup & Run

```bash
cd frontend
npm install
npm run dev
```

The Vite development server runs at [http://localhost:5173](http://localhost:5173).

### 3. Running Automated Test Suite

```bash
cd backend
$env:PYTHONPATH = "."
pytest
```
All **24 tests** across unit, integration, and end-to-end suites pass cleanly.

## Built-In Platform Presets

| Preset | Target Aspect Ratio | Subtitles | Punch Zoom | Background Music | Description |
|---|---|---|---|---|---|
| **reels** | `9:16` Vertical | Burned | Yes (1.15x) | `upbeat_energetic` | Optimized for Instagram Reels and TikTok. |
| **shorts** | `9:16` Vertical | Burned | Yes (1.15x) | `chill_ambient` | Optimized for YouTube Shorts. |
| **youtube**| `original` (16:9) | Burned | No | None / Muted | Long-form landscape video. |
| **square** | `1:1` Square | Optional | No | `chill_ambient` | Square format for LinkedIn/Facebook feed. |

## Documentation

- [`docs/WALKTHROUGH.md`](docs/WALKTHROUGH.md): Step-by-step milestone walkthrough, algorithm breakdown, and verification reports.
- [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md): Technical architecture and implementation specs.
- [`PROJECT_ARCHITECTURE.md`](PROJECT_ARCHITECTURE.md): System architecture and agent pipeline flow.

## Repository Structure

- `backend/`: FastAPI backend (API routes, services, models, tests, audio assets).
- `frontend/`: React + Vite frontend application (dark-themed editorial interface).
- `docs/`: Technical walkthroughs and architectural documentation.
- `jobs/`: Storage directory for job input, analysis, planning, rendering, and validation.
- `test_videos/`: Sample input videos for local development and testing.

