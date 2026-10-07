"""INSPECT stage: verify the rendered file actually matches the plan."""
from pathlib import Path
from typing import List, Optional
from ..models.edl import EDL
from ..models.metadata import VideoMetadata
from ..utils.ffmpeg import FFmpegError, ffprobe_json
from .director import Requirements
from .validator import Issue


def inspect(path: Path, edl: EDL, meta: VideoMetadata, req: Optional[Requirements] = None) -> List[Issue]:
    iss: List[Issue] = []
    if not path.exists() or path.stat().st_size == 0:
        return [Issue(code="NO_OUTPUT", message="Rendered file missing or empty", fixable=True)]
    try:
        data = ffprobe_json(path)
    except FFmpegError:
        return [Issue(code="NOT_PLAYABLE", message="ffprobe could not read the output file", fixable=True)]
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None:
        iss.append(Issue(code="NO_VIDEO_STREAM", message="Output has no video stream"))
    if meta.has_audio and a is None:
        iss.append(Issue(code="NO_AUDIO_STREAM", message="Source had audio but output does not"))
    expected = edl.timeline_duration()
    actual = float(data.get("format", {}).get("duration") or 0)
    if abs(actual - expected) > max(0.6, 0.04 * expected):
        iss.append(Issue(code="DURATION_MISMATCH",
                         message=f"Output is {actual:.2f}s but plan expects {expected:.2f}s"))
    if v is not None and edl.project.width and edl.project.height:
        want = edl.project.width / edl.project.height
        got = int(v.get("width", 1)) / max(int(v.get("height", 1)), 1)
        if abs(want - got) / want > 0.02:
            iss.append(Issue(code="ASPECT_MISMATCH",
                             message=f"Output {v.get('width')}x{v.get('height')} != requested {edl.project.aspect_ratio}"))
    if req and req.target_duration and actual > req.target_duration * 1.15:
        iss.append(Issue(code="OVER_TARGET", message=f"Output {actual:.1f}s exceeds target {req.target_duration:.0f}s"))
    return iss
