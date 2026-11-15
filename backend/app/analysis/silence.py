"""Week 2 / Day 3 — silence detection using FFmpeg silencedetect."""
import re
from pathlib import Path
from typing import List
from .. import config
from ..models.analysis import Silence
from ..models.metadata import VideoMetadata
from ..utils.ffmpeg import run


def detect_silence(path: Path, meta: VideoMetadata) -> List[Silence]:
    if not meta.has_audio:
        return []
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
           "-af", f"silencedetect=noise={config.SILENCE_DB}dB:d={config.SILENCE_MIN}", "-f", "null", "-"]
    stderr = run(cmd).stderr                           # raises FFmpegMissing / FFmpegError (never swallowed here)
    out, start = [], None
    for line in stderr.splitlines():
        m = re.search(r"silence_start:\s*(-?[\d.]+)", line)
        if m:
            start = max(float(m.group(1)), 0.0)
            continue
        m = re.search(r"silence_end:\s*([\d.]+)", line)
        if m and start is not None:
            out.append(Silence(start=round(start, 3), end=round(float(m.group(1)), 3)))
            start = None
    if start is not None:                              # silence runs to the end of file
        out.append(Silence(start=round(start, 3), end=round(meta.duration, 3)))
    return [s for s in out if s.end > s.start]
