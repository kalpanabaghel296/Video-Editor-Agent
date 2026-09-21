# Video Editor Agent — Frontend

React + Vite frontend for the AI Video Editing Agent.

## Features (Week 1)

- Video file picker (accepts mp4, mov, avi, etc.)
- User editing instruction input (textarea)
- Upload & Analyze action calling `POST /upload`
- Loading state with animated indicator
- Error handling display
- Returned Job ID and Job Status display
- Refresh Status button calling `GET /jobs/{job_id}`
- Technical metadata visualization (duration, resolution, FPS, video codec, audio availability and codec)

## Setup & Run

```bash
# Install dependencies
npm install

# Start development server (defaults to port 5173)
npm run dev

# Build for production
npm run build
```

## Backend Connection

By default, the API client connects to `http://127.0.0.1:8000`. You can override this by setting the `VITE_API_URL` environment variable:

```bash
VITE_API_URL=http://localhost:8000 npm run dev
```

