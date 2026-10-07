"""Central configuration. Everything can be overridden with environment variables."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # backend/
PROJECT_ROOT = ROOT.parent
JOBS_DIR = Path(os.getenv("JOBS_DIR", PROJECT_ROOT / "jobs"))
ASSETS_MUSIC_DIR = Path(os.getenv("MUSIC_DIR", PROJECT_ROOT / "assets" / "music"))
FRONTEND_DIR = PROJECT_ROOT / "frontend"

# Transcription
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")         # tiny | base | small | medium | large-v3 (bigger = more accurate, slower)
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", None)       # optional language hint (e.g. en, hi)
WHISPER_BEAM = int(os.getenv("WHISPER_BEAM", "5"))
# Roman-script Hinglish prompt: biases Whisper to write "yeh video mein ..." instead of Devanagari / wrong English
WHISPER_PROMPT = os.getenv("WHISPER_PROMPT", "Hello guys, yeh video mein main AI agent ke baare mein bata rahi hoon. "
                           "Aap koi bhi video dalo, woh prompt se edit ho jayegi.")
TRANSCRIBER = os.getenv("TRANSCRIBER", "auto")               # auto | faster-whisper | whisper | none

# Analysis
SILENCE_DB = float(os.getenv("SILENCE_DB", "-35"))
SILENCE_MIN = float(os.getenv("SILENCE_MIN", "0.5"))
SCENE_THRESHOLD = float(os.getenv("SCENE_THRESHOLD", "27.0"))

# Agent
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "none")            # none | ollama | anthropic
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")

ALLOWED_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
