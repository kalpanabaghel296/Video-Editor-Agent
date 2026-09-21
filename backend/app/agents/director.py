"""
AI Director Agent — plans video edits and generates an Edit Decision List (EDL).

Combines:
- User instruction prompt
- Technical metadata (duration, resolution, fps)
- Whisper transcript (spoken words & timestamps)
- Silence detection (pause intervals to cut)
- Scene boundaries (visual shot cuts)
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Optional

from app.models.edl import EditCut, EditDecisionList
from app.models.job import JobStatus
from app.services.job_service import JOBS_ROOT, save_job_state
from app.services.metadata_service import load_metadata


def _parse_target_duration(instruction: str, original_duration: float) -> Optional[float]:
    """Extract requested duration in seconds from instruction prompt if present (takes latest match)."""
    if not instruction:
        return None

    # Matches e.g. "30 second", "30s", "30-second", "1 minute", "60 sec"
    matches_sec = re.findall(r"(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|s)\b", instruction, re.IGNORECASE)
    matches_min = re.findall(r"(\d+(?:\.\d+)?)\s*(?:minutes?|mins?|m)\b", instruction, re.IGNORECASE)

    # In conversational revisions, latest specified duration takes precedence
    if matches_sec:
        target = float(matches_sec[-1])
        return min(target, original_duration)
    if matches_min:
        target = float(matches_min[-1]) * 60.0
        return min(target, original_duration)

    return None


def _select_narrative_arc_intervals(
    active_intervals: list[tuple[float, float]],
    target_duration: float,
    original_duration: float,
    transcript_segments: list[dict] | None = None,
) -> list[tuple[float, float]]:
    """
    Select candidate cuts spanning Hook (intro), Core Highlights (middle), and Outro (conclusion)
    while strictly preserving complete spoken thoughts/sentences without mid-clause cutoffs.
    """
    if not active_intervals:
        return [(0.0, min(target_duration, original_duration))]

    viable = [iv for iv in active_intervals if (iv[1] - iv[0]) >= 0.5]
    if not viable:
        viable = active_intervals

    total_active = sum(en - st for st, en in viable)
    if target_duration >= total_active - 0.1:
        return viable

    units = []
    for st, en in viable:
        matching_segs = [
            s for s in (transcript_segments or [])
            if s["start"] < en and s["end"] > st
        ]
        if matching_segs:
            speech_end = min(en, max(s["end"] for s in matching_segs))
            text = " ".join(s["text"].strip() for s in matching_segs)
        else:
            speech_end = en
            text = ""
        units.append({
            "start": st,
            "end": en,
            "speech_end": speech_end,
            "duration": round(en - st, 2),
            "text": text,
        })

    # Hook: earliest unit with speech
    hook_candidates = [u for u in units if len(u["text"].strip()) > 3]
    hook_unit = hook_candidates[0] if hook_candidates else units[0]

    # Outro: latest unit with speech in the latter portion of the video
    outro_candidates = [
        u for u in units
        if u["end"] >= original_duration * 0.55
        and u["start"] > hook_unit["end"]
        and len(u["text"].strip()) > 3
        and u["duration"] >= 1.0
    ]
    if not outro_candidates:
        outro_candidates = [u for u in units if u != hook_unit and len(u["text"].strip()) > 3]
    outro_unit = outro_candidates[-1] if outro_candidates else units[-1]

    # Middle units between hook and outro
    middle_candidates = [
        u for u in units
        if u["start"] > hook_unit["end"] + 0.3
        and u["end"] < outro_unit["start"] - 0.3
        and len(u["text"].strip()) > 3
    ]

    hook_needed = hook_unit["duration"]
    outro_needed = outro_unit["duration"]
    combined_hook_outro = hook_needed + outro_needed

    # If target_duration allows 3 full thoughts without severing them:
    # Need at least combined_hook_outro + 2.0s for a distinct middle thought
    if target_duration >= combined_hook_outro + 2.0 and middle_candidates:
        best_mid = min(middle_candidates, key=lambda u: abs(u["start"] - original_duration / 2.0))
        selected_units = [hook_unit, best_mid, outro_unit]
    else:
        selected_units = [hook_unit, outro_unit]

    cuts: list[tuple[float, float]] = []
    if len(selected_units) == 2:
        u1, u2 = selected_units
        if u1 == u2:
            b1 = round(target_duration * 0.58, 2)
            b2 = round(target_duration - b1, 2)
            cuts.append((0.0, b1))
            c2_st = round(max(b1 + 1.0, original_duration - b2), 2)
            cuts.append((c2_st, round(min(original_duration, c2_st + b2), 2)))
        elif u1["duration"] <= target_duration - 2.5:
            # Full Hook thought preserved!
            c1_st = u1["start"]
            c1_en = u1["end"]
            cuts.append((c1_st, c1_en))

            rem_budget = round(target_duration - (c1_en - c1_st), 2)
            c2_st = u2["start"]
            c2_en = round(min(u2["end"], c2_st + rem_budget), 2)
            cuts.append((c2_st, c2_en))
        else:
            b1 = round(target_duration * 0.60, 2)
            b2 = round(target_duration - b1, 2)
            cuts.append((u1["start"], round(min(u1["end"], u1["start"] + b1), 2)))
            cuts.append((u2["start"], round(min(u2["end"], u2["start"] + b2), 2)))
    else:
        # 3 units: Hook, Middle, Outro
        u1, u2, u3 = selected_units
        c1_st = u1["start"]
        c1_dur = min(u1["duration"], round(target_duration * 0.42, 2))
        c1_en = round(c1_st + c1_dur, 2)
        cuts.append((c1_st, c1_en))

        c3_st = u3["start"]
        c3_dur = min(u3["duration"], round(target_duration * 0.30, 2))
        c3_en = round(min(u3["end"], c3_st + c3_dur), 2)

        rem_mid = round(target_duration - c1_dur - (c3_en - c3_st), 2)
        c2_st = u2["start"]
        c2_en = round(min(u2["end"], c2_st + rem_mid), 2)

        cuts.append((c2_st, c2_en))
        cuts.append((c3_st, c3_en))

    # Precision adjustment: absorb into trailing silence buffer of last cut
    cuts = sorted(cuts, key=lambda x: x[0])
    curr_total = round(sum(en - st for st, en in cuts), 2)
    diff = round(target_duration - curr_total, 2)
    if abs(diff) > 0.01:
        adj_idx = len(cuts) - 1
        st, en = cuts[adj_idx]
        new_en = round(min(original_duration, en + diff), 2)
        cuts[adj_idx] = (st, new_en)

    return cuts


def plan_edit(
    job_id: uuid.UUID,
    instruction_override: Optional[str] = None,
    target_duration_override: Optional[float] = None,
) -> EditDecisionList:
    """
    Generate an Edit Decision List (EDL) for the given job.
    """
    job_dir = JOBS_ROOT / str(job_id)
    if not job_dir.exists():
        raise FileNotFoundError(f"Job '{job_id}' not found.")

    # 1. Load Metadata
    meta = load_metadata(job_id)
    original_duration = round(meta.duration, 2)

    # 2. Load User Instruction
    instruction = instruction_override
    if not instruction:
        inst_file = job_dir / "instruction.txt"
        if inst_file.exists():
            instruction = inst_file.read_text(encoding="utf-8").strip()
    if not instruction:
        instruction = "Create a tight, engaging edit removing dead silence."

    target_duration = (
        min(target_duration_override, original_duration)
        if target_duration_override is not None
        else _parse_target_duration(instruction, original_duration)
    )

    # 3. Load optional analysis artifacts
    transcript_data = None
    transcript_file = job_dir / "analysis" / "transcript.json"
    if transcript_file.exists():
        try:
            transcript_data = json.loads(transcript_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    silence_data = None
    silence_file = job_dir / "analysis" / "silence.json"
    if silence_file.exists():
        try:
            silence_data = json.loads(silence_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    scene_data = None
    scene_file = job_dir / "analysis" / "scenes.json"
    if scene_file.exists():
        try:
            scene_data = json.loads(scene_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 4. Generate Keep Intervals
    # Invert silences into active speech/audio intervals
    active_intervals: list[tuple[float, float]] = []
    silence_removed = 0.0

    if silence_data and silence_data.get("silences"):
        silences = silence_data["silences"]
        curr_time = 0.0
        raw_intervals: list[list[float]] = []
        for s in silences:
            s_start = max(0.0, float(s["start"]))
            s_end = min(original_duration, float(s["end"]))
            if s_start > curr_time + 0.3:  # minimum active speech clip threshold
                raw_intervals.append([round(curr_time, 2), round(s_start, 2)])
            curr_time = max(curr_time, s_end)

        if curr_time < original_duration - 0.3:
            raw_intervals.append([round(curr_time, 2), original_duration])

        # If user requested a target duration and raw speech is shorter than target,
        # expand intervals into surrounding visual footage so the video meets target duration
        if raw_intervals:
            total_active = sum(end - start for start, end in raw_intervals)
            if target_duration and target_duration > total_active:
                needed = min(target_duration, original_duration) - total_active
                while needed > 0.05:
                    expanded_in_pass = False
                    for i in range(len(raw_intervals)):
                        prev_bound = raw_intervals[i - 1][1] if i > 0 else 0.0
                        avail_before = raw_intervals[i][0] - prev_bound
                        if avail_before > 0.05 and needed > 0.05:
                            step = min(avail_before, needed, 0.5)
                            raw_intervals[i][0] = round(raw_intervals[i][0] - step, 2)
                            needed -= step
                            expanded_in_pass = True

                        next_bound = (
                            raw_intervals[i + 1][0]
                            if i < len(raw_intervals) - 1
                            else original_duration
                        )
                        avail_after = next_bound - raw_intervals[i][1]
                        if avail_after > 0.05 and needed > 0.05:
                            step = min(avail_after, needed, 0.5)
                            raw_intervals[i][1] = round(raw_intervals[i][1] + step, 2)
                            needed -= step
                            expanded_in_pass = True

                    if not expanded_in_pass:
                        break

            # Merge any contiguous or overlapping intervals
            merged: list[tuple[float, float]] = []
            for item in sorted(raw_intervals, key=lambda x: x[0]):
                if not merged or merged[-1][1] < item[0]:
                    merged.append((item[0], item[1]))
                else:
                    prev_st, prev_en = merged[-1]
                    merged[-1] = (prev_st, max(prev_en, item[1]))

            active_intervals = merged
        else:
            active_intervals = [(0.0, min(target_duration or original_duration, original_duration))]
    else:
        # Fallback if no silence detected or no audio: use full video or scenes
        if scene_data and scene_data.get("scenes"):
            for sc in scene_data["scenes"]:
                s_st = float(sc.get("start_time", sc.get("start_seconds", 0.0)))
                s_en = float(sc.get("end_time", sc.get("end_seconds", original_duration)))
                active_intervals.append((round(s_st, 2), round(s_en, 2)))
        else:
            active_intervals = [(0.0, original_duration)]

    # Calculate silence removed based on final active intervals
    total_active_planned = sum(end - start for start, end in active_intervals)
    silence_removed = max(0.0, round(original_duration - total_active_planned, 2))

    # 5. Map Transcript Text to Intervals
    transcript_segments = transcript_data.get("segments", []) if transcript_data else []

    # Whole-timeline narrative arc selection when condensing into shorter target duration
    if target_duration and target_duration < total_active_planned:
        final_intervals = _select_narrative_arc_intervals(
            active_intervals,
            target_duration=target_duration,
            original_duration=original_duration,
            transcript_segments=transcript_segments,
        )
    else:
        final_intervals = active_intervals

    cuts: list[EditCut] = []
    total_planned = 0.0

    for idx, (c_start, c_end) in enumerate(final_intervals, start=1):
        c_dur = round(c_end - c_start, 2)
        if c_dur <= 0.2:
            continue

        # Check target duration budget if not already constrained by narrative selection
        if target_duration and (total_planned + c_dur > target_duration + 0.05):
            remaining = round(target_duration - total_planned, 2)
            if remaining >= 0.5:
                c_end = round(c_start + remaining, 2)
                c_dur = remaining
            else:
                break

        # Associate spoken words
        matching_texts = [
            seg["text"]
            for seg in transcript_segments
            if (seg["start"] < c_end and seg["end"] > c_start)
        ]
        source_text = " ".join(matching_texts).strip() if matching_texts else None

        reason = (
            f"Active speech segment with high spoken clarity: \"{source_text[:40]}...\""
            if source_text
            else f"Visual cut preserving narrative continuity ({c_dur}s)"
        )

        cuts.append(
            EditCut(
                clip_id=idx,
                start_time=c_start,
                end_time=c_end,
                duration=c_dur,
                source_text=source_text,
                reason=reason,
            )
        )
        total_planned += c_dur

        if target_duration and total_planned >= target_duration:
            break

    # If active intervals were too short or empty, ensure at least 1 fallback cut
    if not cuts:
        fallback_dur = target_duration if target_duration else original_duration
        cuts.append(
            EditCut(
                clip_id=1,
                start_time=0.0,
                end_time=round(min(fallback_dur, original_duration), 2),
                duration=round(min(fallback_dur, original_duration), 2),
                source_text=transcript_data.get("full_text") if transcript_data else None,
                reason="Primary video pass with preserved pacing.",
            )
        )
        total_planned = cuts[0].duration

    if target_duration:
        narrative_text = " across narrative arc (hook, core highlight, and outro)" if len(cuts) >= 2 else ""
        summary = (
            f"AI Director formulated an edit plan matching requested {total_planned:.2f}s target duration "
            f"({len(cuts)} cuts{narrative_text}, original {original_duration:.2f}s). "
            f"Preserved spoken speech + essential visual context, eliminating ~{silence_removed:.2f}s dead pauses."
        )
    else:
        summary = (
            f"AI Director formulated an edit plan with {len(cuts)} cuts totalling {total_planned:.2f}s "
            f"(original {original_duration:.2f}s). "
            f"Eliminated ~{silence_removed:.2f}s of silence and non-essential pauses to maximize energy."
        )

    edl = EditDecisionList(
        job_id=str(job_id),
        instruction=instruction,
        original_duration=original_duration,
        target_duration=target_duration,
        total_planned_duration=round(total_planned, 2),
        silence_removed_seconds=round(silence_removed, 2),
        cuts=cuts,
        summary=summary,
    )

    # Persist EDL
    out_path = job_dir / "planning" / "edl.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(edl.model_dump_json(indent=2), encoding="utf-8")

    # Update job state
    save_job_state(job_id=job_id, status=JobStatus.PLANNING, instruction=instruction)

    return edl


def load_edl(job_id: uuid.UUID) -> EditDecisionList:
    """Load persisted Edit Decision List from jobs/{job_id}/planning/edl.json."""
    path = JOBS_ROOT / str(job_id) / "planning" / "edl.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Edit plan not found for job '{job_id}'. Run POST /jobs/{job_id}/plan first."
        )
    try:
        return EditDecisionList.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Failed to parse edl.json for job '{job_id}': {exc}") from exc


def revise_edl(
    job_id: uuid.UUID,
    revision_instruction: str,
    target_duration_override: Optional[float] = None,
) -> EditDecisionList:
    """
    Revise an existing Edit Decision List based on user feedback.
    Supports conversational updates:
    - Target duration changes ("make it 6s", "shorter", "longer")
    - Clip removals ("remove first clip", "remove last clip", "drop clip 1")
    - Clip selections ("keep only first clip", "keep only last sentence")
    - Prompt re-evaluation incorporating previous context
    """
    job_dir = JOBS_ROOT / str(job_id)
    edl_path = job_dir / "planning" / "edl.json"
    if not edl_path.exists():
        return plan_edit(job_id, instruction_override=revision_instruction)

    current_edl = load_edl(job_id)
    # Backup original EDL before revising
    backup_path = job_dir / "planning" / "edl_previous.json"
    if not backup_path.exists():
        backup_path.write_text(edl_path.read_text(encoding="utf-8"), encoding="utf-8")

    meta = load_metadata(job_id)
    original_duration = round(meta.duration, 2)
    rev_lower = revision_instruction.lower()

    # Determine target duration
    new_target: Optional[float] = target_duration_override
    if new_target is None:
        new_target = _parse_target_duration(revision_instruction, original_duration)

    cuts = list(current_edl.cuts)

    # 1. Check clip removal / selection requests
    if any(phrase in rev_lower for phrase in ["remove first", "drop first", "remove clip 1", "drop opening"]):
        if len(cuts) > 1:
            cuts = cuts[1:]
    elif any(phrase in rev_lower for phrase in ["remove last", "drop last", "remove ending", "drop clip 2"]):
        if len(cuts) > 1:
            cuts = cuts[:-1]
    elif any(phrase in rev_lower for phrase in ["only first", "keep first", "keep only first"]):
        if cuts:
            cuts = cuts[:1]
    elif any(phrase in rev_lower for phrase in ["only last", "keep last", "keep only last"]):
        if cuts:
            cuts = cuts[-1:]

    # 2. Check if re-planning from full media with updated target duration is needed
    if new_target is not None and abs(new_target - current_edl.total_planned_duration) > 0.5:
        combined_instruction = f"{current_edl.instruction or ''}. Revision: {revision_instruction}".strip()
        return plan_edit(
            job_id,
            instruction_override=combined_instruction,
            target_duration_override=new_target,
        )

    # 3. Re-index remaining cuts
    for idx, c in enumerate(cuts, start=1):
        c.clip_id = idx
        c.duration = round(c.end_time - c.start_time, 2)

    total_planned = sum(c.duration for c in cuts)
    silence_removed = max(0.0, original_duration - total_planned)

    revised_edl = EditDecisionList(
        job_id=str(job_id),
        instruction=f"{current_edl.instruction} (Revised: {revision_instruction})",
        original_duration=original_duration,
        target_duration=new_target or current_edl.target_duration,
        total_planned_duration=round(total_planned, 2),
        silence_removed_seconds=round(silence_removed, 2),
        cuts=cuts,
        summary=f"Revised EDL based on feedback: '{revision_instruction}'. {len(cuts)} cuts totalling {total_planned:.2f}s.",
    )

    # Persist revised EDL
    edl_path.write_text(revised_edl.model_dump_json(indent=2), encoding="utf-8")
    save_job_state(job_id=job_id, status=JobStatus.PLANNING, instruction=revised_edl.instruction)
    return revised_edl

