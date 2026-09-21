"""
Subtitle synthesis & timeline re-mapping engine.

Re-times original Whisper transcript segments to match the newly edited
video timeline after dead silences and unwanted sections are cut out.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from app.models.edl import EditCut


def _format_srt_timestamp(seconds: float) -> str:
    """Format float seconds into standard SubRip timestamp (HH:MM:SS,mmm)."""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds % 1) * 1000))
    if ms >= 1000:
        s += 1
        ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_retimed_subtitles(
    transcript_segments: list[dict[str, Any]],
    cuts: list[EditCut],
) -> list[dict[str, Any]]:
    """
    Map original transcript words/segments onto the compressed timeline
    of the edited video.

    Parameters
    ----------
    transcript_segments : list[dict]
        Original segments with 'start', 'end', and 'text'.
    cuts : list[EditCut]
        Ordered list of kept video cuts.

    Returns
    -------
    list[dict]
        List of re-timed segments: [{'start': float, 'end': float, 'text': str}]
    """
    retimed: list[dict[str, Any]] = []
    accumulated_offset = 0.0

    for cut in cuts:
        c_start = cut.start_time
        c_end = cut.end_time
        c_dur = cut.duration

        for seg in transcript_segments:
            s_start = float(seg.get("start", 0.0))
            s_end = float(seg.get("end", 0.0))
            text = seg.get("text", "").strip()
            if not text:
                continue

            # Check overlap between cut interval and speech segment
            overlap_start = max(c_start, s_start)
            overlap_end = min(c_end, s_end)

            if overlap_end - overlap_start >= 0.1:  # At least 100ms overlap
                new_start = round(accumulated_offset + (overlap_start - c_start), 3)
                new_end = round(accumulated_offset + (overlap_end - c_start), 3)
                retimed.append(
                    {
                        "start": new_start,
                        "end": new_end,
                        "text": text,
                    }
                )

        accumulated_offset += c_dur

    return retimed


def format_srt(retimed_segments: list[dict[str, Any]]) -> str:
    """Format re-timed segments into standard .srt text."""
    if not retimed_segments:
        return ""

    lines: list[str] = []
    for idx, seg in enumerate(retimed_segments, start=1):
        st = _format_srt_timestamp(seg["start"])
        en = _format_srt_timestamp(seg["end"])
        lines.append(f"{idx}")
        lines.append(f"{st} --> {en}")
        lines.append(seg["text"])
        lines.append("")  # Blank line separator

    return "\n".join(lines).strip() + "\n"


def save_srt(retimed_segments: list[dict[str, Any]], output_path: Path) -> Path:
    """Save re-timed subtitles to a .srt file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    srt_content = format_srt(retimed_segments)
    output_path.write_text(srt_content, encoding="utf-8")
    return output_path
