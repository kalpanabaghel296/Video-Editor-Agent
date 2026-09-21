# Project Architecture & System Design

Autonomous multi-modal AI video editing architecture with clear separation of concerns.

```
+-------------------------------------------------------------------------------+
|                             CLIENT / USER LAYER                               |
|       React + Vite Frontend (Timeline, Audio Mixer, Preset Selector, Chat)     |
+-------------------------------------------------------------------------------+
                                      │  REST API Calls
                                      ▼
+-------------------------------------------------------------------------------+
|                               FASTAPI BACKEND                                 |
|                                                                               |
|  [API Endpoints]                                                              |
|   ├── /upload               -> Ingestion & Media Persistence                  |
|   ├── /jobs/{id}/auto-edit  -> Autonomous One-Click Pipeline Execution        |
|   ├── /jobs/{id}/revise     -> Conversational Feedback Loop                   |
|   └── /presets              -> Platform Presets (Reels, Shorts, Square, etc.) |
|                                                                               |
|  [Agent Layer]                                                                |
|   ├── Orchestrator          -> Coordinates Observe -> Plan -> Act -> Validate |
|   ├── AI Director           -> Formulates Edit Decision List (EDL)            |
|   └── Quality Validator     -> Inspects exported MP4 metrics                  |
|                                                                               |
|  [Media Understanding Engine]                                                 |
|   ├── FFprobe Metadata      -> Codec, dimensions, fps, duration               |
|   ├── Local Whisper STT     -> Word-level timestamps & speech segments        |
|   ├── FFmpeg silencedetect  -> Dead pauses & audio gaps extraction            |
|   └── PySceneDetect         -> Visual scene boundary fallback                 |
|                                                                               |
|  [High-Performance Render Engine (FFmpeg Single-Pass)]                         |
|   ├── Frame-Accurate Cutting & Concat                                         |
|   ├── Social Re-framing (9:16 vertical & 1:1 square blurred canvas)           |
|   ├── Visual Punch-in Zoom (1.15x on alternating cuts)                        |
|   ├── Dynamic Subtitle Re-timing & Burn-in                                    |
|   └── Background Music Mixing + Sidechain Ducking                             |
+-------------------------------------------------------------------------------+
                                      │
                                      ▼
+-------------------------------------------------------------------------------+
|                            STORAGE & JOBS LAYOUT                              |
|  jobs/{job_id}/                                                               |
|   ├── input/video.mp4           <- Original raw uploaded footage              |
|   ├── metadata/media.json       <- Technical video & audio stream metadata    |
|   ├── analysis/                 <- Silence intervals, Whisper transcripts     |
|   ├── planning/edl.json         <- Edit Decision List with cut rationale      |
|   ├── output/final_edit.mp4     <- Rendered export MP4                        |
|   ├── output/subtitles.srt      <- Re-timed subtitles file                    |
|   └── validation/report.json    <- Post-render compliance verification        |
+-------------------------------------------------------------------------------+
```

## Directory Structure & Responsibilities

- **`backend/app/agents/`**:
  - `orchestrator.py`: Coordinates the full autonomous editing pipeline and revision loops.
  - `director.py`: AI Director that plans EDL cuts, resolves narrative arc, and preserves context.
  - `validator.py`: Automated quality validator checking duration, file size, and stream integrity.
- **`backend/app/api/`**: FastAPI REST route controllers (`upload`, `metadata`, `transcription`, `silence`, `scenes`, `planning`, `rendering`, `orchestration`).
- **`backend/app/editing/`**: Video processing modules:
  - `cuts.py`: Generates the single-pass FFmpeg `-filter_complex` string with social reframing and punch-in zoom.
  - `audio.py`: Audio mixing, royalty-free track resolution, and sidechain compression ducking.
  - `captions.py`: Re-timed subtitle generation and `.srt` serialization.
  - `renderer.py`: Manages the FFmpeg subprocess execution and error-handling retries.
- **`backend/app/models/`**: Strongly typed Pydantic data schemas (`job`, `media`, `transcript`, `scene`, `edl`, `validation`, `orchestration`).
- **`backend/app/services/`**: Low-level disk persistence, FFprobe parsing, silence detection, and Whisper execution.
- **`backend/tests/`**: Comprehensive pytest test suite (24 passing unit/integration tests).
- **`frontend/`**: React 18 + Vite frontend with real-time video playback, visual waveform, and platform preset selector.
