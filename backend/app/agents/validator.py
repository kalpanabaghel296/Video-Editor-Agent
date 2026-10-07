"""Plan validator + patcher. Finds problems in an EDL and patches only the broken parts."""
from pathlib import Path
from typing import List, Optional, Tuple
from pydantic import BaseModel, ValidationError
from ..editing.captions import build_captions
from ..models.edl import EDL
from ..models.metadata import VideoMetadata
from ..models.transcript import Transcript
from .director import Requirements


class Issue(BaseModel):
    code: str
    message: str
    severity: str = "error"          # error | warning
    path: Optional[str] = None       # e.g. "clips[2].source_end"
    fixable: bool = True


MIN_CLIP = 0.2


def validate_raw(data: dict) -> Tuple[Optional[EDL], List[Issue]]:
    """Schema-level check for EDL dicts (e.g. coming from an LLM)."""
    try:
        return EDL(**data), []
    except ValidationError as e:
        return None, [Issue(code="SCHEMA", message=err["msg"], path=".".join(str(p) for p in err["loc"]),
                            fixable=False) for err in e.errors()]


def validate(edl: EDL, meta: VideoMetadata, req: Optional[Requirements] = None) -> List[Issue]:
    iss: List[Issue] = []
    dur = meta.duration
    if not edl.clips:
        iss.append(Issue(code="NO_CLIPS", message="EDL has no clips", path="clips"))
    prev_end = -1.0
    for i, c in enumerate(edl.clips):
        p = f"clips[{i}]"
        if c.source_start < 0:
            iss.append(Issue(code="NEG_START", message=f"{c.id}: start {c.source_start} < 0", path=p + ".source_start"))
        if c.source_end > dur + 0.01:
            iss.append(Issue(code="END_BEYOND", message=f"{c.id}: end {c.source_end:.2f}s > source duration {dur:.2f}s",
                             path=p + ".source_end"))
        if c.source_end <= c.source_start:
            iss.append(Issue(code="BAD_RANGE", message=f"{c.id}: start >= end", path=p))
        elif c.duration < MIN_CLIP:
            iss.append(Issue(code="TOO_SHORT", message=f"{c.id}: only {c.duration:.2f}s long", path=p, severity="warning"))
        if c.source_start < prev_end - 1e-6:
            iss.append(Issue(code="OVERLAP", message=f"{c.id}: overlaps/out of order", path=p, severity="warning"))
        prev_end = max(prev_end, c.source_end)
    ids = {c.id for c in edl.clips}
    for i, e in enumerate(edl.effects):
        if e.clip_id not in ids:
            iss.append(Issue(code="EFFECT_CLIP", message=f"effect refers to unknown clip {e.clip_id}", path=f"effects[{i}]"))
    for i, t in enumerate(edl.transitions):
        if t.clip_id not in ids:
            iss.append(Issue(code="TRANSITION_CLIP", message=f"transition refers to unknown clip {t.clip_id}",
                             path=f"transitions[{i}]"))
    total = edl.timeline_duration()
    for i, c in enumerate(edl.captions):
        if c.end > total + 0.5 or c.start < 0 or c.end <= c.start:
            iss.append(Issue(code="CAPTION_RANGE", message=f"caption {i} outside timeline", path=f"captions[{i}]",
                             severity="warning"))
    for i, o in enumerate(edl.overlays):
        if o.start < 0 or o.end <= o.start or o.start >= total:
            iss.append(Issue(code="OVERLAY_RANGE", message=f"overlay {i} outside timeline", path=f"overlays[{i}]",
                             severity="warning"))
    if edl.audio.music and edl.audio.music_path and not Path(edl.audio.music_path).exists():
        iss.append(Issue(code="MUSIC_MISSING", message="music file not found", path="audio.music_path", severity="warning"))
    if req and req.target_duration and total > req.target_duration * 1.05:
        iss.append(Issue(code="OVER_TARGET", message=f"{total:.1f}s exceeds target {req.target_duration:.0f}s",
                         path="project.duration"))
    if edl.project.width <= 0 or edl.project.height <= 0 or edl.project.width % 2 or edl.project.height % 2:
        iss.append(Issue(code="BAD_RESOLUTION", message="output resolution must be positive and even", path="project"))
    return iss


def patch(edl: EDL, meta: VideoMetadata, req: Optional[Requirements] = None,
          transcript: Optional[Transcript] = None) -> Tuple[EDL, List[str]]:
    """Clamp / drop / sort / trim — never restarts the whole job, only fixes broken parts."""
    applied: List[str] = []
    dur = meta.duration
    clips = []
    for c in edl.clips:
        s, e = max(c.source_start, 0.0), min(c.source_end, dur)
        if (s, e) != (c.source_start, c.source_end):
            applied.append(f"Clamped {c.id} to [{s:.2f}, {e:.2f}]")
        if e - s < MIN_CLIP:
            applied.append(f"Dropped {c.id} (too short / invalid)")
            continue
        c.source_start, c.source_end = round(s, 3), round(e, 3)
        clips.append(c)
    clips.sort(key=lambda c: c.source_start)
    fixed = []
    for c in clips:                                       # remove overlaps
        if fixed and c.source_start < fixed[-1].source_end:
            c.source_start = fixed[-1].source_end
            if c.source_end - c.source_start < MIN_CLIP:
                applied.append(f"Dropped {c.id} (fully overlapped)")
                continue
            applied.append(f"Trimmed overlap on {c.id}")
        fixed.append(c)
    clips = fixed
    if not clips:                                         # last resort: keep whole video
        from ..models.edl import Clip
        clips = [Clip(id="c1", source_start=0.0, source_end=dur, label="full video")]
        applied.append("No valid clips left — falling back to the full video")
    if req and req.target_duration:
        left, kept = req.target_duration * 1.0, []
        for c in clips:
            if left <= 0.05:
                applied.append(f"Dropped {c.id} (over target)")
                continue
            if c.duration > left:
                c.source_end = round(c.source_start + left, 3)
                applied.append(f"Trimmed {c.id} to fit target {req.target_duration:.0f}s")
            left -= c.duration
            kept.append(c)
        clips = kept
    edl.clips = clips
    ids = {c.id for c in clips}
    edl.effects = [e for e in edl.effects if e.clip_id in ids]
    edl.transitions = [t for t in edl.transitions if t.clip_id in ids]
    if edl.audio.music and edl.audio.music_path and not Path(edl.audio.music_path).exists():
        edl.audio.music_path = None
        applied.append("Music file missing — using built-in track")
    w, h = edl.project.width, edl.project.height
    if w <= 0 or h <= 0 or w % 2 or h % 2:
        edl.project.width, edl.project.height = max(w // 2 * 2, 2), max(h // 2 * 2, 2)
        applied.append("Fixed output resolution to even values")
    edl.project.duration = edl.timeline_duration()
    fixed_ov = []
    for o in edl.overlays:
        o.start, o.end = max(o.start, 0.0), min(o.end, edl.project.duration)
        if o.end - o.start >= 0.3:
            fixed_ov.append(o)
    edl.overlays = fixed_ov
    edl.audio.sfx_events = [e for e in edl.audio.sfx_events if 0 <= e.time < edl.project.duration - 0.05]
    if edl.caption_settings.enabled and req and req.caption_script:
        from ..editing.captions import script_to_captions
        edl.captions = script_to_captions(req.caption_script, edl.project.duration)
    elif edl.caption_settings.enabled and transcript is not None:
        edl.captions = build_captions(edl.clips, transcript)       # keep captions in sync with clips
    else:
        edl.captions = [c for c in edl.captions if 0 <= c.start < c.end <= edl.project.duration + 0.5]
    return edl, applied
