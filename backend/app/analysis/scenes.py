"""Week 2 / Day 2 — scene boundaries with PySceneDetect (ContentDetector)."""
from pathlib import Path
from typing import List
from .. import config
from ..models.analysis import Scene
from ..models.metadata import VideoMetadata


def _sec(t):
    return t.seconds if hasattr(t, "seconds") else t.get_seconds()


def _frame(t):
    return t.frame_num if hasattr(t, "frame_num") else t.get_frames()


def detect_scenes(path: Path, meta: VideoMetadata) -> List[Scene]:
    whole = [Scene(index=0, start=0.0, end=meta.duration, start_frame=0,
                   end_frame=int(meta.duration * meta.fps))]
    try:
        from scenedetect import ContentDetector, detect       # type: ignore
        found = detect(str(path), ContentDetector(threshold=config.SCENE_THRESHOLD))
    except Exception:
        return whole
    if not found:
        return whole
    return [Scene(index=i, start=round(_sec(s), 3), end=round(_sec(e), 3),
                  start_frame=_frame(s), end_frame=_frame(e))
            for i, (s, e) in enumerate(found)]
