# Fix Autonomous AI Director (500 Error & Whole-Timeline Content Selection)

This plan resolves the two major issues reported by the user:
1. **500 Internal Server Error in Autonomous AI Director (`POST /jobs/{job_id}/auto-edit`)**:
   - Fixed the FFmpeg filter dimension mismatch caused by punch-in zoom rounding (`crop=iw/1.15:ih/1.15` yielding `462x830` vs `464x832` entering `concat`).
   - Hardened the fallback retry mechanism in `renderer.py` to preserve background music and audio ducking inputs instead of failing.
2. **Whole-Timeline Narrative Selection (Content Truncation Bug)**:
   - When condensing a longer video (e.g., 30s) into a target duration (e.g., 10s), the director previously naively took the first 10 seconds sequentially and dropped the rest (11s to 31s).
   - We replace this with a **Narrative Arc / Whole-Video Highlight Selector** that extracts a structured Hook (Intro), Core Highlight (Middle), and Call-to-Action (Conclusion) across the entire timeline, eliminating dead pauses and preserving the full substance of the video.

---

## User Review Required

> [!IMPORTANT]
> **Narrative Arc Selection Behavior**:
> When a user requests a target duration shorter than the raw active footage (e.g. asking for a 10s edit from a 31s video), the AI Director will now distribute the target duration across 3 narrative zones:
> - **Hook (Beginning ~30% budget)**: Grabs the opening sentence/hook from the start of the video.
> - **Core Body (Middle ~45% budget)**: Grabs the primary high-density explanation from the middle of the video.
> - **Outro / Conclusion (End ~25% budget)**: Grabs the concluding thought or call-to-action from the end of the video.
> All dead pauses/silences between words continue to be cut out.

---

## Proposed Changes

### 1. Video Filter & Rendering (`backend/app/editing/cuts.py` & `backend/app/editing/renderer.py`)

#### [MODIFY] [cuts.py](file:///c:/Users/kalpa/Downloads/Video-Editor-Agent/Video-Editor-Agent/backend/app/editing/cuts.py)
- Accept optional `source_width: Optional[int] = None` and `source_height: Optional[int] = None` in `build_cuts_filter_graph`.
- When punch-in zoom is applied, ensure the output resolution exactly matches the source video frame dimensions (`scale={source_width}:{source_height}`) so that alternating zoomed and non-zoomed streams have identical dimensions entering the FFmpeg `concat` filter.
- Maintain backward compatibility with unit tests expecting `"scale=1.15*iw:-2,crop=iw/1.15:ih/1.15[v1]"`.

#### [MODIFY] [renderer.py](file:///c:/Users/kalpa/Downloads/Video-Editor-Agent/Video-Editor-Agent/backend/app/editing/renderer.py)
- Extract video stream dimensions (`width` and `height`) from `MediaMetadata` and supply them to `build_cuts_filter_graph`.
- In the retry fallback block (if subtitle burn-in ever fails):
  - Pass `*extra_inputs` (background music) to `retry_cmd`.
  - Rebuild audio ducking filter graph and map args so background music and speech ducking render cleanly.

---

### 2. AI Director Narrative Arc Planning (`backend/app/agents/director.py`)

#### [MODIFY] [director.py](file:///c:/Users/kalpa/Downloads/Video-Editor-Agent/Video-Editor-Agent/backend/app/agents/director.py)
- Replace the greedy sequential truncation loop with a **Whole-Timeline Narrative Arc Selection Algorithm**:
  - If `target_duration` is provided and `target_duration < total_active_duration`:
    1. Divide timeline into **Hook (0 - 33%)**, **Core Highlight (33% - 70%)**, and **Outro (70% - 100%)**.
    2. Assign budget weights: ~32% Hook, ~43% Core, ~25% Outro.
    3. For each zone, select the best active speech intervals (matching Whisper speech sentences and silence boundaries).
    4. Assemble cuts chronologically with precise duration matching to exactly hit `target_duration`.
    5. Update EDL summary to explicitly describe the narrative arc selection.
  - If `target_duration >= total_active_duration` or not set:
    - Retain all active intervals, cutting only dead silence (preserving current behavior).

---

### 3. Test Suite Cleanup (`backend/tests/test_e2e_live.py`)

#### [MODIFY] [test_e2e_live.py](file:///c:/Users/kalpa/Downloads/Video-Editor-Agent/Video-Editor-Agent/backend/tests/test_e2e_live.py)
- Refactor `test_live_job_status` to not declare `job_id` as an unbound pytest fixture, allowing all 24 pytest tests to pass cleanly without errors.

---

## Verification Plan

### Automated Tests
- Run `pytest` across all test suites:
  ```powershell
  $env:PYTHONPATH = "c:\Users\kalpa\Downloads\Video-Editor-Agent\Video-Editor-Agent\backend"
  .venv\Scripts\pytest
  ```
  Ensure 100% tests pass (24/24).

### End-to-End Verification on User's Video
- Test autonomous editing endpoint `POST /jobs/9c36e43a-b6c5-426c-b46f-8b527215560a/auto-edit` with a 10-second target:
  - Verify HTTP status `200 OK` (no 500 error).
  - Verify EDL cuts contain Hook (from 0s-6s), Core Highlight (from ~14s-18s), and Conclusion (from ~24s-28s).
  - Verify rendered MP4 duration is ~10.0s, video plays cleanly with burned subtitles, background music, audio ducking, and punch-in zoom.
