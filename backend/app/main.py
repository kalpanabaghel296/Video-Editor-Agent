"""FastAPI application entrypoint for the AI Video Editing Agent."""

from fastapi import FastAPI

app = FastAPI(title="Video Editor Agent API")


@app.get("/health")
def health_check() -> dict[str, str]:
    """Basic health endpoint for service availability checks."""
    return {"status": "ok"}
