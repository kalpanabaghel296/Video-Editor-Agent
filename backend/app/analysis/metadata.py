"""Week 1 — metadata extraction via ffprobe."""
from pathlib import Path
from ..models.metadata import VideoMetadata
from ..utils.ffmpeg import ffprobe_json


def _fps(rate: str) -> float:
    try:
        n, d = rate.split("/")
        return round(float(n) / float(d), 3) if float(d) else 0.0
    except Exception:
        return 0.0


def extract_metadata(path: Path) -> VideoMetadata:
    data = ffprobe_json(path)
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None:
        raise ValueError("File mein video stream nahi mili (not a valid video)")
    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or v.get("duration") or 0)
    w, h = int(v.get("width", 0)), int(v.get("height", 0))
    rot = 0
    try:
        rot = int(v.get("tags", {}).get("rotate", 0))
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(sd["rotation"])
    except Exception:
        pass
    if abs(rot) in (90, 270):          # ffmpeg auto-rotates, so report display size
        w, h = h, w
    return VideoMetadata(
        duration=round(duration, 3), width=w, height=h,
        fps=_fps(v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1") or 30.0,
        has_audio=a is not None,
        video_codec=v.get("codec_name"),
        audio_codec=a.get("codec_name") if a else None,
        audio_sample_rate=int(a["sample_rate"]) if a and a.get("sample_rate") else None,
        size_bytes=int(fmt.get("size") or 0),
    )
