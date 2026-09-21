# Video Editor Agent — Backend

FastAPI backend providing video ingestion, metadata extraction, job lifecycle tracking, and analysis endpoints.

## Prerequisites

- Python 3.10+
- FFmpeg and FFprobe installed and accessible via `PATH`.

## Setup & Run

```bash
# 1. Create and activate virtual environment
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On macOS/Linux:
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run FastAPI application (port 8000)
uvicorn app.main:app --reload
```

Interactive OpenAPI docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

## Key Endpoints

- `GET /health`: Health check endpoint.
- `POST /upload`: Multipart video upload + optional instruction; creates job, extracts metadata via FFprobe, persists `media.json`.
- `GET /jobs/{job_id}`: Returns job status (`pending`, `completed`, `failed`).
- `POST /jobs/{job_id}/metadata/extract`: Triggers FFprobe extraction on `jobs/{job_id}/input/video.mp4`.
- `GET /jobs/{job_id}/metadata`: Returns persisted metadata JSON.
- `POST /jobs/{job_id}/scenes/detect`: Runs PySceneDetect on input video.
- `GET /jobs/{job_id}/scenes`: Returns detected scene cuts.

## Tests

```bash
# Run API test suite
pytest tests/test_api.py -v

# Run Whisper smoke test
python tests/test_whisper_smoke.py
```

