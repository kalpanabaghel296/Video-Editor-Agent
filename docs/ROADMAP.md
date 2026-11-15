# Roadmap

What is done, what is next. (Older notes: `IMPLEMENTATION_PLAN.md` and `WALKTHROUGH.md` describe an *Autonomous AI Director* (`POST /jobs/{id}/auto-edit`, `editing/cuts.py`, a hook / core / call-to-action "narrative arc" selector) that is **not present in this code base**. It is kept below as an idea.)

## Done
Weeks 1-6: upload, analysis, director, planner, EDL, renderer, validator / inspector loop, brain cuts, noise removal, music, SFX, look & effects, studio UI.
Week 7: best-take selection, face-aware reframing, chat revise, reference style matching, platform presets, caption looks, Hinglish prompts, FFmpeg detection, upload limits (see `week7done.md`).

## Next (suggested order)
1. **Measure on real data** - run 5-10 real videos (portrait + landscape webcam). Record: face found %, best-take correct / wrong, caption word accuracy (`WHISPER_MODEL=small` vs `medium`, `WHISPER_LANGUAGE=hi`). Put the numbers in the project report.
2. **Narrative-arc selector** (from `IMPLEMENTATION_PLAN.md`): when condensing a long video to a short one, pick a hook (intro), the core highlight and a call to action across the whole timeline instead of the first N seconds. `select_mode="best"` already scores segments; this would add structure.
3. **Hook / title card** for the first 2-3 seconds (the title overlay already exists).
4. **Better face detector** (e.g. an ONNX YuNet model) if Haar misses faces on your footage; the detector is one function (`editing/reframe.detect_face`).
5. **Beat-synced cuts** to the music track.
6. **Queue / ETA** in the UI (the pool has 2 workers; a third job waits silently today).
7. **Undo / version history** of EDLs per job.
8. **LLM director** end-to-end evaluation (`LLM_PROVIDER=ollama|anthropic`) against the rule parser on ~10 prompts.
