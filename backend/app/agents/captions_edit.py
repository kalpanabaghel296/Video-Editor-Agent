"""Manual caption editing: the user changes / adds / deletes caption lines on the OUTPUT timeline.

The edited lines are saved in jobs/<id>/assets/captions_override.json together with a fingerprint of the clips they were
made for. A caption-only re-render (loop.rerender_captions) uses them straight away; a full re-run re-applies them only
while the clips are still the same - otherwise the timing would be wrong, so they are dropped with a warning.
"""
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models.edl import EDL, Caption, CaptionWord

MAX_LINES = 500


def clips_sig(edl: EDL) -> str:
    raw = json.dumps([[round(c.source_start, 2), round(c.source_end, 2)] for c in edl.clips] + [edl.caption_settings.language or ""])
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _file(job_dir: Path) -> Path:
    return job_dir / "assets" / "captions_override.json"


def _spread(text: str, start: float, end: float) -> List[CaptionWord]:
    toks = text.split()
    weight = sum(len(t) + 1 for t in toks) or 1
    t, out = start, []
    for tok in toks:
        d = (end - start) * (len(tok) + 1) / weight
        out.append(CaptionWord(text=tok, start=round(t, 3), end=round(t + d, 3)))
        t += d
    return out


def build(items: List[Dict[str, Any]], edl: EDL) -> List[Caption]:
    """items = [{start, end, text}] -> clean, sorted, non-overlapping Captions inside the timeline.
    Word timings of an unchanged-length line are kept (so the highlight animation stays accurate)."""
    total = edl.timeline_duration()
    old = list(edl.captions)
    out: List[Caption] = []
    for it in items[:MAX_LINES]:
        text = " ".join(str(it.get("text", "")).split())
        try:
            s, e = float(it.get("start", 0)), float(it.get("end", 0))
        except (TypeError, ValueError):
            continue
        s, e = max(s, 0.0), min(e, total) if total else e
        if not text or e - s < 0.1:
            continue
        prev = next((c for c in old if abs(c.start - s) < 0.06 and abs(c.end - e) < 0.06 and c.words
                     and len(c.words) == len(text.split())), None)
        words = [CaptionWord(text=t, start=w.start, end=w.end) for t, w in zip(text.split(), prev.words)] if prev \
            else _spread(text, s, e)
        out.append(Caption(start=round(s, 3), end=round(e, 3), text=text, words=words))
    out.sort(key=lambda c: c.start)
    for a, b in zip(out, out[1:]):                       # never overlap
        if a.end > b.start:
            a.end = max(a.start + 0.1, b.start - 0.01)
            a.words = [w for w in a.words if w.start < a.end]
            if a.words:
                a.words[-1].end = min(a.words[-1].end, a.end)
    return out


def save(job_dir: Path, edl: EDL, caps: List[Caption]) -> None:
    f = _file(job_dir)
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps({"sig": clips_sig(edl), "captions": [c.model_dump() for c in caps]}, ensure_ascii=False),
                 encoding="utf-8")


def clear(job_dir: Path) -> None:
    _file(job_dir).unlink(missing_ok=True)


def apply_override(edl: EDL, job_dir: Path) -> Optional[str]:
    """Put the user's captions into a freshly planned EDL. Returns a message when something has to be reported."""
    f = _file(job_dir)
    if not f.exists() or not edl.caption_settings.enabled:
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if data.get("sig") != clips_sig(edl):
            f.unlink(missing_ok=True)
            return ("Clips / timing / caption language badal gayi, isliye tumhare manually edit kiye captions reset ho gaye - "
                    "naye captions transcript se bane hain. Captions tab mein dobara edit kar sakte ho.")
        edl.captions = [Caption(**c) for c in data["captions"]]
        edl.explanation.append(f"Used your {len(edl.captions)} manually edited caption line(s)")
    except Exception:
        return None
    return None