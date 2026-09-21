"""
Subclip and filter-complex builder — turns an EditDecisionList into an
accurate, single-pass FFmpeg filter graph.
"""

from __future__ import annotations

from typing import Optional, Tuple
from app.models.edl import EditCut, EditDecisionList


def build_cuts_filter_graph(
    cuts: list[EditCut],
    has_audio: bool = True,
    aspect_ratio: str = "original",
    enable_punch_in: bool = False,
    source_width: Optional[int] = None,
    source_height: Optional[int] = None,
) -> Tuple[str, list[str]]:
    """
    Construct the FFmpeg ``-filter_complex`` string and output mapping arguments
    to accurately extract and concatenate all subclips in a single pass, with optional
    social-media aspect ratio re-framing (9:16 vertical or 1:1 square) and visual
    punch-in zoom for talking-head retention.

    Parameters
    ----------
    cuts : list[EditCut]
        Ordered list of subclips to keep.
    has_audio : bool
        Whether the input media has an audio stream.
    aspect_ratio : str
        Target aspect ratio: 'original', '9:16' (Shorts/Reels), or '1:1' (Square).
    enable_punch_in : bool
        Whether to apply subtle 1.15x camera punch-in zoom on cuts.
    source_width : Optional[int]
        Original video width in pixels, used to ensure exact dimension matching on punch zoom.
    source_height : Optional[int]
        Original video height in pixels, used to ensure exact dimension matching on punch zoom.

    Returns
    -------
    Tuple[str, list[str]]
        (filter_complex_string, map_args)
    """
    if not cuts:
        raise ValueError("Cannot build filter graph with empty cuts list.")

    filter_parts: list[str] = []
    n = len(cuts)

    needs_reframing = aspect_ratio in ("9:16", "1:1")
    raw_v_out = "v_raw" if needs_reframing else "outv"

    scale_match = f",scale={source_width}:{source_height}" if (source_width and source_height) else ""
    punch_filter = f",scale=1.15*iw:-2,crop=iw/1.15:ih/1.15{scale_match}"

    if n == 1:
        c = cuts[0]
        st = max(0.0, round(c.start_time, 3))
        en = round(c.end_time, 3)
        zoom_suffix = punch_filter if enable_punch_in else ""
        v_filter = f"[0:v]trim=start={st}:end={en},setpts=PTS-STARTPTS{zoom_suffix}[{raw_v_out}]"
        filter_parts.append(v_filter)

        if has_audio:
            a_filter = f"[0:a]atrim=start={st}:end={en},asetpts=PTS-STARTPTS[outa]"
            filter_parts.append(a_filter)
    else:
        # Multiple cuts: generate trim for each, then concat
        concat_in_v = []
        concat_in_a = []

        for idx, c in enumerate(cuts):
            st = max(0.0, round(c.start_time, 3))
            en = round(c.end_time, 3)

            v_label = f"v{idx}"
            # Apply punch-in zoom on alternating clips (1, 3, 5...)
            zoom_suffix = punch_filter if (enable_punch_in and idx % 2 == 1) else ""
            filter_parts.append(
                f"[0:v]trim=start={st}:end={en},setpts=PTS-STARTPTS{zoom_suffix}[{v_label}]"
            )
            concat_in_v.append(f"[{v_label}]")

            if has_audio:
                a_label = f"a{idx}"
                filter_parts.append(
                    f"[0:a]atrim=start={st}:end={en},asetpts=PTS-STARTPTS[{a_label}]"
                )
                concat_in_a.append(f"[{a_label}]")

        if has_audio:
            concat_inputs = "".join(
                f"{concat_in_v[i]}{concat_in_a[i]}" for i in range(n)
            )
            filter_parts.append(f"{concat_inputs}concat=n={n}:v=1:a=1[{raw_v_out}][outa]")
        else:
            concat_inputs = "".join(concat_in_v)
            filter_parts.append(f"{concat_inputs}concat=n={n}:v=1:a=0[{raw_v_out}]")

    # Apply aspect ratio re-framing if requested
    if aspect_ratio == "9:16":
        # 1080x1920 vertical canvas with blurred background fill
        reframing_filter = (
            f"[{raw_v_out}]split=2[bg_src][fg_src];"
            f"[bg_src]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:5[bg_blur];"
            f"[fg_src]scale=1080:1920:force_original_aspect_ratio=decrease[fg_fit];"
            f"[bg_blur][fg_fit]overlay=(W-w)/2:(H-h)/2[outv]"
        )
        filter_parts.append(reframing_filter)
    elif aspect_ratio == "1:1":
        # 1080x1080 square canvas with blurred background fill
        reframing_filter = (
            f"[{raw_v_out}]split=2[bg_src][fg_src];"
            f"[bg_src]scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080,boxblur=20:5[bg_blur];"
            f"[fg_src]scale=1080:1080:force_original_aspect_ratio=decrease[fg_fit];"
            f"[bg_blur][fg_fit]overlay=(W-w)/2:(H-h)/2[outv]"
        )
        filter_parts.append(reframing_filter)

    if has_audio:
        map_args = ["-map", "[outv]", "-map", "[outa]"]
    else:
        map_args = ["-map", "[outv]"]

    return ";".join(filter_parts), map_args
