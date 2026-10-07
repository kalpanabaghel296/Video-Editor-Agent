"""FastAPI app: upload -> analysis -> prompt -> edited video."""
import json
import re
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

# Suppress benign ConnectionResetError on Windows when browser seeks / drops video stream chunks
if sys.platform == "win32":
    try:
        from asyncio.proactor_events import _ProactorBasePipeTransport
        _orig_call_connection_lost = _ProactorBasePipeTransport._call_connection_lost

        def _safe_call_connection_lost(self, exc=None):
            try:
                _orig_call_connection_lost(self, exc)
            except ConnectionResetError:
                pass

        _ProactorBasePipeTransport._call_connection_lost = _safe_call_connection_lost
    except Exception:
        pass
from pathlib import Path
from typing import Iterator, List, Optional
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from . import config
from .agents import loop, planner
from .analysis.pipeline import load_analysis
from .editing.captions import transcript_to_captions, write_srt, write_vtt
from .models.edl import EDL
from .models.transcript import Segment, Transcript, Word
from .services.jobs import JobStore
from .utils.ffmpeg import FFmpegError, ffprobe_json, run as ff_run

app = FastAPI(title="AI Video Editing Agent", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
store = JobStore(config.JOBS_DIR)
pool = ThreadPoolExecutor(max_workers=2)
_running: set = set()
_guard = threading.Lock()


def _job(job_id: str) -> dict:
    if not store.exists(job_id):
        raise HTTPException(404, "job not found")
    return store.load(job_id)


def _ranged(path: Path, request: Request, media_type: str = "video/mp4") -> Response:
    """Serve a file with HTTP Range support (works on every Starlette version).
    Browsers need this to play + seek video reliably, with audio, straight from the <video> tag."""
    size = path.stat().st_size
    base = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    rng = request.headers.get("range")
    if not rng:
        return FileResponse(path, media_type=media_type, headers=base)
    m = re.match(r"bytes=(\d*)-(\d*)", rng.strip())
    if not m or (not m.group(1) and not m.group(2)):
        return FileResponse(path, media_type=media_type, headers=base)
    if m.group(1):
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else size - 1
    else:                                                  # suffix range: last N bytes
        start, end = max(size - int(m.group(2)), 0), size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        return Response(status_code=416, headers={**base, "Content-Range": f"bytes */{size}"})
    length = end - start + 1

    def chunks() -> Iterator[bytes]:
        with open(path, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                buf = f.read(min(1024 * 256, left))
                if not buf:
                    break
                left -= len(buf)
                yield buf
    return StreamingResponse(chunks(), status_code=206, media_type=media_type,
                             headers={**base, "Content-Range": f"bytes {start}-{end}/{size}",
                                      "Content-Length": str(length)})


def _submit(job_id: str, fn, *args) -> None:
    with _guard:
        if job_id in _running:
            raise HTTPException(409, "job already running")
        _running.add(job_id)

    def work():
        try:
            fn(*args)
        finally:
            with _guard:
                _running.discard(job_id)
    store.update(job_id, busy=True)
    pool.submit(work)


def _analyze_only(job_id: str) -> None:
    try:
        loop.ensure_observed(store, job_id)
        store.update(job_id, status="analyzed", busy=False)
    except Exception as e:
        store.update(job_id, status="failed", busy=False, error=str(e))
        store.log(job_id, f"FAILED analysis: {e}")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/jobs")
async def create_job(file: UploadFile = File(...), instruction: Optional[str] = Form(None),
                     music: Optional[UploadFile] = File(None)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in config.ALLOWED_EXT:
        raise HTTPException(400, f"Unsupported file type '{ext}'. Allowed: {sorted(config.ALLOWED_EXT)}")
    job_id = store.create(file.filename or f"video{ext}")
    dest = store.path(job_id) / "source" / f"input{ext}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    if music is not None and music.filename:
        _save_music(job_id, music)
    if instruction and instruction.strip():
        _submit(job_id, loop.run_agent, store, job_id, instruction.strip())
    else:
        _submit(job_id, _analyze_only, job_id)
    return store.load(job_id)


class EditRequest(BaseModel):
    instruction: str
    retranscribe: bool = False          # redo Whisper (e.g. after switching to a bigger model)


@app.post("/api/jobs/{job_id}/edit")
def edit(job_id: str, body: EditRequest):
    _job(job_id)
    if not body.instruction.strip():
        raise HTTPException(400, "instruction is empty")
    if body.retranscribe:
        _reset_transcript(job_id)
    _submit(job_id, loop.run_agent, store, job_id, body.instruction.strip())
    return store.load(job_id)


def _reset_transcript(job_id: str) -> None:
    a = store.path(job_id) / "analysis"
    for name in ("transcript.json", "fillers.json", "source_captions.json", "source.vtt", "source.srt", "errors.json"):
        (a / name).unlink(missing_ok=True)


class SegIn(BaseModel):
    start: float
    end: float
    text: str


class TranscriptIn(BaseModel):
    segments: List[SegIn]


@app.put("/api/jobs/{job_id}/transcript")
def save_transcript(job_id: str, body: TranscriptIn):
    """Manual correction of the transcript -> guaranteed-accurate captions on the next run."""
    _job(job_id)
    if job_id in _running:
        raise HTTPException(409, "job is running")
    a = store.path(job_id) / "analysis"
    tp = a / "transcript.json"
    old = Transcript(**json.loads(tp.read_text(encoding="utf-8"))) if tp.exists() else Transcript()
    by_time = {(round(x.start, 2), round(x.end, 2)): x for x in old.segments}
    segs = []
    for sg in body.segments:
        text = " ".join(sg.text.split())
        if not text or sg.end <= sg.start:
            continue
        prev = by_time.get((round(sg.start, 2), round(sg.end, 2)))
        words: List[Word] = []
        toks = text.split()
        if prev and prev.words and len(prev.words) == len(toks):      # same word count: keep real timings
            words = [Word(text=t, start=w.start, end=w.end) for t, w in zip(toks, prev.words)]
        segs.append(Segment(start=sg.start, end=sg.end, text=text, words=words))
    new = Transcript(language=old.language, duration=old.duration, segments=segs, note=None)
    tp.write_text(new.model_dump_json(indent=2), encoding="utf-8")
    for name in ("fillers.json", "source_captions.json", "source.vtt", "source.srt", "errors.json"):
        (a / name).unlink(missing_ok=True)
    store.log(job_id, f"Transcript corrected manually ({len(segs)} segments)")
    return {"segments": len(segs)}


MUSIC_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def _save_music(job_id: str, up: UploadFile) -> str:
    ext = Path(up.filename or "").suffix.lower()
    if ext not in MUSIC_EXT:
        raise HTTPException(400, f"Unsupported music type '{ext}'. Allowed: {sorted(MUSIC_EXT)}")
    assets = store.path(job_id) / "assets"
    assets.mkdir(exist_ok=True)
    for old in assets.glob("music.*"):
        old.unlink()
    with open(assets / f"music{ext}", "wb") as f:
        shutil.copyfileobj(up.file, f)
    store.log(job_id, f"Background music uploaded: {up.filename}")
    return up.filename or f"music{ext}"


@app.post("/api/jobs/{job_id}/music")
def upload_music(job_id: str, file: UploadFile = File(...)):
    """Your own background track for this job (used when the instruction asks for music)."""
    _job(job_id)
    return {"music": _save_music(job_id, file)}


@app.delete("/api/jobs/{job_id}/music")
def delete_music(job_id: str):
    _job(job_id)
    for old in (store.path(job_id) / "assets").glob("music.*"):
        old.unlink()
    return {"music": None}


@app.get("/api/jobs/{job_id}/audio-check")
def audio_check(job_id: str, kind: str = "final"):
    """Does the video FILE really contain audible sound? (independent of any browser / player problem)"""
    _job(job_id)
    p = {"source": lambda: store.source_file(job_id), "draft": lambda: store.path(job_id) / "output" / "draft.mp4",
         "final": lambda: store.path(job_id) / "output" / "final.mp4"}.get(kind)
    if p is None:
        raise HTTPException(400, "kind must be source|draft|final")
    p = Path(p())
    if not p.exists():
        raise HTTPException(404, f"{kind} video not ready")
    info = ffprobe_json(p)
    a = next((x for x in info.get("streams", []) if x.get("codec_type") == "audio"), None)
    if a is None:
        return {"has_audio": False, "verdict": "no_audio", "message": "The file has NO audio track."}

    def stderr(af: str) -> str:
        try:
            return ff_run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(p), "-vn", "-af", af, "-f", "null", "-"]).stderr
        except FFmpegError as e:
            return e.stderr

    vd = stderr("volumedetect")
    m, x = re.search(r"mean_volume:\s*(-?[\d.]+)", vd), re.search(r"max_volume:\s*(-?[\d.]+)", vd)
    mean_db, max_db = (float(m.group(1)) if m else None), (float(x.group(1)) if x else None)
    eb = stderr("ebur128=peak=true")
    lu = re.findall(r"I:\s*(-?[\d.]+)\s*LUFS", eb)
    lufs = float(lu[-1]) if lu else None
    if mean_db is None or mean_db < -55 or (max_db is not None and max_db < -40):
        verdict, msg = "silent", "The audio track exists but is (almost) silent."
    elif (lufs is not None and lufs < -30) or mean_db < -38:
        verdict, msg = "very_quiet", "Audio is present but very quiet - raise the player / system volume."
    else:
        verdict, msg = "ok", "Audio is present and at a normal level - the sound is inside the file."
    return {"has_audio": True, "codec": a.get("codec_name"), "channels": a.get("channels"),
            "sample_rate": a.get("sample_rate"), "duration": float(info.get("format", {}).get("duration") or 0),
            "mean_db": mean_db, "peak_db": max_db, "lufs": lufs, "verdict": verdict, "message": msg}


@app.get("/api/jobs")
def list_jobs():
    return store.list()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return _job(job_id)


@app.get("/api/jobs/{job_id}/analysis")
def get_analysis(job_id: str):
    _job(job_id)
    if not (store.path(job_id) / "analysis" / "metadata.json").exists():
        raise HTTPException(404, "analysis not ready")
    an = load_analysis(store.path(job_id))
    sil_total = sum(s.duration for s in an.silences)
    cap_file = store.path(job_id) / "analysis" / "source_captions.json"
    source_caps = []
    if cap_file.exists():
        try:
            source_caps = json.loads(cap_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    elif an.transcript.segments:
        source_caps = [c.model_dump() for c in transcript_to_captions(an.transcript)]
    return {"metadata": an.metadata, "transcript_note": an.transcript.note, "language": an.transcript.language,
            "transcript_snippet": " ".join(s.text for s in an.transcript.segments)[:400],
            "segments": len(an.transcript.segments), "scene_count": len(an.scenes),
            "transcript_segments": [{"start": x.start, "end": x.end, "text": x.text} for x in an.transcript.segments],
            "silence_count": len(an.silences), "silence_seconds": round(sil_total, 1),
            "filler_count": len(an.fillers), "captions": source_caps, "raw": an}


@app.get("/api/jobs/{job_id}/edl")
def get_edl(job_id: str):
    p = store.path(job_id) / "edl.json"
    if not p.exists():
        raise HTTPException(404, "EDL not ready")
    return json.loads(p.read_text(encoding="utf-8"))


@app.get("/api/jobs/{job_id}/edl/preview")
def get_edl_preview(job_id: str):
    p = store.path(job_id) / "edl.json"
    if not p.exists():
        raise HTTPException(404, "EDL not ready")
    return planner.edl_preview(EDL(**json.loads(p.read_text(encoding="utf-8"))))


@app.get("/api/jobs/{job_id}/log")
def get_log(job_id: str):
    _job(job_id)
    return {"lines": store.read_log(job_id)}


@app.get("/api/jobs/{job_id}/video")
def get_video(job_id: str, request: Request, kind: str = "final"):
    _job(job_id)
    path = {"source": lambda: store.source_file(job_id),
            "draft": lambda: store.path(job_id) / "output" / "draft.mp4",
            "final": lambda: store.path(job_id) / "output" / "final.mp4"}.get(kind)
    if path is None:
        raise HTTPException(400, "kind must be source|draft|final")
    p = path()
    if not Path(p).exists():
        raise HTTPException(404, f"{kind} video not ready")
    return _ranged(Path(p), request)


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str):
    p = store.path(job_id) / "output" / "final.mp4"
    if not p.exists():
        raise HTTPException(404, "final video not ready")
    return FileResponse(p, media_type="video/mp4", filename=f"edited_{job_id}.mp4")


@app.get("/api/jobs/{job_id}/captions")
def get_captions(job_id: str, kind: str = "source", format: str = "vtt"):
    _job(job_id)
    jd = store.path(job_id)
    fmt = format.lower()
    if fmt not in ("vtt", "srt", "json"):
        raise HTTPException(400, "format must be vtt, srt, or json")

    if kind == "source":
        target = jd / "analysis" / f"source.{fmt}"
        if not target.exists() and (jd / "analysis" / "transcript.json").exists():
            an = load_analysis(jd)
            caps = transcript_to_captions(an.transcript)
            if fmt == "vtt":
                write_vtt(caps, target)
            elif fmt == "srt":
                write_srt(caps, target)
            elif fmt == "json":
                return [c.model_dump() for c in caps]
        if fmt == "json":
            if (jd / "analysis" / "source_captions.json").exists():
                return json.loads((jd / "analysis" / "source_captions.json").read_text(encoding="utf-8"))
            return []
        if not target.exists():
            raise HTTPException(404, f"source {fmt} captions not ready")
        media = "text/vtt; charset=utf-8" if fmt == "vtt" else "text/plain; charset=utf-8"
        return FileResponse(target, media_type=media)

    elif kind in ("final", "edited", "draft"):
        target = jd / "output" / f"final.{fmt}"
        if not target.exists() and (jd / "work" / f"captions.{fmt}").exists():
            target = jd / "work" / f"captions.{fmt}"
        if not target.exists() and (jd / "edl.json").exists():
            from .models.edl import Caption
            edl_data = json.loads((jd / "edl.json").read_text(encoding="utf-8"))
            caps = [Caption(**c) for c in edl_data.get("captions", [])]
            if fmt == "vtt":
                write_vtt(caps, jd / "output" / "final.vtt")
                target = jd / "output" / "final.vtt"
            elif fmt == "srt":
                write_srt(caps, jd / "output" / "final.srt")
                target = jd / "output" / "final.srt"
            elif fmt == "json":
                return [c.model_dump() for c in caps]
        if fmt == "json":
            if (jd / "edl.json").exists():
                edl_data = json.loads((jd / "edl.json").read_text(encoding="utf-8"))
                return edl_data.get("captions", [])
            return []
        if not target.exists():
            raise HTTPException(404, f"{kind} {fmt} captions not ready")
        media = "text/vtt; charset=utf-8" if fmt == "vtt" else "text/plain; charset=utf-8"
        return FileResponse(target, media_type=media)
    else:
        raise HTTPException(400, "kind must be source or final")


@app.get("/api/jobs/{job_id}/captions/download")
def download_captions(job_id: str, kind: str = "source", format: str = "srt"):
    _job(job_id)
    jd = store.path(job_id)
    fmt = format.lower()
    if fmt not in ("vtt", "srt"):
        raise HTTPException(400, "format must be vtt or srt")
    get_captions(job_id, kind=kind, format=fmt)
    target = (jd / "analysis" / f"source.{fmt}") if kind == "source" else (jd / "output" / f"final.{fmt}")
    if not target.exists() and kind != "source":
        target = jd / "work" / f"captions.{fmt}"
    if not target.exists():
        raise HTTPException(404, f"captions not found for {kind}")
    media = "text/vtt; charset=utf-8" if fmt == "vtt" else "text/plain; charset=utf-8"
    return FileResponse(target, media_type=media, filename=f"captions_{kind}_{job_id}.{fmt}")


if config.FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(config.FRONTEND_DIR), html=True), name="frontend")