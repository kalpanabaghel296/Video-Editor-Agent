"""OBSERVE stage orchestrator: metadata -> transcript -> scenes -> silence -> fillers.

Each stage caches its JSON under jobs/<id>/analysis/. A stage failing (except metadata)
never kills the job: the error is recorded and the planner works with what exists.
"""
import json
from pathlib import Path
from typing import Callable, Dict, Optional
from ..editing.captions import transcript_to_captions, write_srt, write_vtt
from ..models.analysis import AnalysisBundle, Filler, Scene, Silence
from ..models.metadata import VideoMetadata
from ..models.transcript import Transcript
from . import fillers, fumbles, metadata, scenes, silence, transcription

Progress = Callable[[str, str], None]


def _write(p: Path, obj) -> None:
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def run_observe(job_dir: Path, source: Path, on_progress: Optional[Progress] = None,
                force: bool = False) -> Dict[str, str]:
    a = job_dir / "analysis"
    a.mkdir(parents=True, exist_ok=True)
    work = job_dir / "work"
    status: Dict[str, str] = {}
    errors: Dict[str, str] = {}

    def note(stage: str, state: str):
        status[stage] = state
        if on_progress:
            on_progress(stage, state)

    def cached(name: str) -> bool:
        return (a / name).exists() and not force

    # 1. metadata (fatal on failure)
    note("metadata", "running")
    if not cached("metadata.json"):
        meta = metadata.extract_metadata(source)
        _write(a / "metadata.json", meta.model_dump())
    meta = VideoMetadata(**json.loads((a / "metadata.json").read_text(encoding="utf-8")))
    note("metadata", "done")

    # 2. transcript
    note("transcript", "running")
    if not cached("transcript.json"):
        try:
            tr = transcription.transcribe(source, work, meta.duration)
        except Exception as e:
            tr = Transcript(duration=meta.duration, note=f"transcript unavailable: {e}")
            errors["transcript"] = str(e)
        _write(a / "transcript.json", tr.model_dump())
    else:
        try:
            tr = Transcript(**json.loads((a / "transcript.json").read_text(encoding="utf-8")))
        except Exception:
            tr = Transcript(duration=meta.duration)
    if "transcript" not in errors and tr.segments:
        try:
            source_caps = transcript_to_captions(tr)
            write_vtt(source_caps, a / "source.vtt")
            write_srt(source_caps, a / "source.srt")
            _write(a / "source_captions.json", [c.model_dump() for c in source_caps])
        except Exception as e:
            errors["source_captions"] = str(e)
    note("transcript", "failed" if "transcript" in errors else "done")

    # 3. scenes
    note("scenes", "running")
    if not cached("scenes.json"):
        try:
            sc = scenes.detect_scenes(source, meta)
        except Exception as e:
            errors["scenes"] = str(e)
            sc = []
        _write(a / "scenes.json", [s.model_dump() for s in sc])
    note("scenes", "failed" if "scenes" in errors else "done")

    # 4. silence
    note("silence", "running")
    if not cached("silence.json"):
        try:
            si = silence.detect_silence(source, meta)
        except Exception as e:
            errors["silence"] = str(e)
            si = []
        _write(a / "silence.json", [s.model_dump() for s in si])
    note("silence", "failed" if "silence" in errors else "done")

    # 5. fillers (derived from transcript)
    note("fillers", "running")
    if not cached("fillers.json"):
        try:
            tr = Transcript(**json.loads((a / "transcript.json").read_text(encoding="utf-8")))
            _write(a / "fillers.json", [f.model_dump() for f in fillers.detect_fillers(tr)])
        except Exception as e:
            errors["fillers"] = str(e)
            _write(a / "fillers.json", [])
    note("fillers", "failed" if "fillers" in errors else "done")

    if errors:
        _write(a / "errors.json", errors)
    return status


def load_analysis(job_dir: Path) -> AnalysisBundle:
    """Common loader — the director/planner get everything from one place."""
    a = job_dir / "analysis"

    def j(name, default):
        p = a / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

    meta = VideoMetadata(**j("metadata.json", {}))
    tr = Transcript(**j("transcript.json", {}))
    sil = [Silence(**x) for x in j("silence.json", [])]
    fil = [Filler(**x) for x in j("fillers.json", [])]
    try:                                                   # the editor's brain: pauses, stutters, retakes ...
        fum = fumbles.detect_fumbles(tr, sil, fil, meta.duration)
    except Exception:
        fum = []
    return AnalysisBundle(metadata=meta, transcript=tr, scenes=[Scene(**x) for x in j("scenes.json", [])],
                          silences=sil, fillers=fil, fumbles=fum)