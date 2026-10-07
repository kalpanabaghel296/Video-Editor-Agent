"""The agent loop: OBSERVE -> UNDERSTAND -> PLAN -> ACT -> INSPECT -> REVISE -> EXPORT.

REVISE only patches the broken part (clamp a bad timestamp, drop a bad clip, degrade an
effect) and re-renders — it never restarts the whole job. After MAX_RETRIES the job is
marked 'failed' with a clear reason.
"""
import json
import shutil
import traceback
from ..analysis.pipeline import load_analysis, run_observe
from ..config import MAX_RETRIES
from ..editing.renderer import RenderError, render
from ..services.jobs import JobStore
from . import director, inspector, planner, validator


def _write_edl(jd, edl):
    (jd / "edl.json").write_text(edl.model_dump_json(indent=2), encoding="utf-8")


def ensure_observed(store: JobStore, job_id: str, force: bool = False) -> None:
    jd = store.path(job_id)
    store.set_stage(job_id, "observe", "running")
    status = run_observe(jd, store.source_file(job_id), force=force,
                         on_progress=lambda st, s: store.log(job_id, f"  observe/{st}: {s}"))
    failed = [k for k, v in status.items() if v == "failed"]
    store.set_stage(job_id, "observe", "done", ("partial — " + ", ".join(failed) + " failed") if failed else "")


def run_agent(store: JobStore, job_id: str, instruction: str) -> None:
    jd = store.path(job_id)
    try:
        store.reset_agent_stages(job_id)
        store.update(job_id, instruction=instruction, busy=True)
        ensure_observed(store, job_id)
        an = load_analysis(jd)
        src = str(store.source_file(job_id))

        store.set_stage(job_id, "understand", "running")
        req = director.understand(instruction, an.metadata)
        store.update(job_id, requirements=req.model_dump())
        store.set_stage(job_id, "understand", "done", f"via {req.source}")

        store.set_stage(job_id, "plan", "running")
        edl = planner.plan(req, an, src)
        issues = validator.validate(edl, an.metadata, req)
        if any(i.severity == "error" for i in issues):
            edl, applied = validator.patch(edl, an.metadata, req, an.transcript)
            for a in applied:
                store.log(job_id, f"  patch: {a}")
            edl.explanation += [f"Auto-fixed: {a}" for a in applied]
        _write_edl(jd, edl)
        store.set_stage(job_id, "plan", "done", f"{len(edl.clips)} clips, {edl.project.duration:.1f}s")

        level, last_problem, result = 0, "", None
        for attempt in range(1, MAX_RETRIES + 1):
            store.update(job_id, attempts=attempt)
            store.set_stage(job_id, "act", "running", f"attempt {attempt}/{MAX_RETRIES}, level {level}")
            try:
                result = render(edl, jd, level=level)
            except RenderError as e:
                last_problem = str(e)
                store.set_stage(job_id, "act", "failed", last_problem)
                store.set_stage(job_id, "revise", "running", "render failed — degrading effects")
                level = min(level + 1, 3)
                continue
            store.set_stage(job_id, "act", "done", "; ".join(result.warnings))

            store.set_stage(job_id, "inspect", "running")
            problems = inspector.inspect(result.path, edl, an.metadata, req)
            errs = [p for p in problems if p.severity == "error"]
            if not errs:
                store.set_stage(job_id, "inspect", "done", "all checks passed")
                break
            last_problem = "; ".join(p.message for p in errs)
            store.set_stage(job_id, "inspect", "failed", last_problem)
            store.set_stage(job_id, "revise", "running", last_problem)
            codes = {p.code for p in errs}
            if "OVER_TARGET" in codes:
                edl, applied = validator.patch(edl, an.metadata, req, an.transcript)
                _write_edl(jd, edl)
            if "ASPECT_MISMATCH" in codes:
                edl.project.fit = "crop"
            level = min(level + 1, 3)
            result = None
        else:
            raise RuntimeError(f"Gave up after {MAX_RETRIES} attempts: {last_problem}")
        if result is None:
            raise RuntimeError(f"Gave up after {MAX_RETRIES} attempts: {last_problem}")
        if store.load(job_id)["stages"]["revise"]["status"] == "running":
            store.set_stage(job_id, "revise", "done", f"fixed after {store.load(job_id)['attempts']} attempt(s)")
        else:
            store.set_stage(job_id, "revise", "skipped", "not needed")

        store.set_stage(job_id, "export", "running")
        final = jd / "output" / "final.mp4"
        shutil.copy(result.path, final)
        if level > 0:
            edl.explanation.append(f"Simplified rendering to level {level} to guarantee a valid output")
        edl.explanation += result.warnings
        _write_edl(jd, edl)
        store.update(job_id, status="done", busy=False, warnings=result.warnings, explanation=edl.explanation,
                     outputs={"draft": "output/draft.mp4", "final": "output/final.mp4"})
        store.set_stage(job_id, "export", "done")
    except Exception as e:                                   # never crash the worker thread
        store.log(job_id, traceback.format_exc())
        store.update(job_id, status="failed", busy=False, error=str(e))
        store.log(job_id, f"FAILED: {e}")
