"""Job manager: per-job folder + job.json state + log. Tracks every agent stage."""
import json
import os
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

STAGES = ["observe", "understand", "plan", "act", "inspect", "revise", "export"]
STATUS_FOR_STAGE = {"observe": "analyzing", "understand": "planning", "plan": "planning", "act": "rendering",
                    "inspect": "validating", "revise": "revising", "export": "exporting"}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class JobStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: Dict[str, threading.RLock] = defaultdict(threading.RLock)

    # ---- paths -------------------------------------------------------------
    def path(self, job_id: str) -> Path:
        return self.root / job_id

    def exists(self, job_id: str) -> bool:
        return (self.path(job_id) / "job.json").exists()

    # ---- state -------------------------------------------------------------
    def create(self, filename: str) -> str:
        job_id = uuid.uuid4().hex[:12]
        for sub in ("source", "analysis", "output", "work"):
            (self.path(job_id) / sub).mkdir(parents=True, exist_ok=True)
        state = {"id": job_id, "filename": filename, "created": _now(), "status": "uploaded", "stage": None,
                 "stages": {s: {"status": "pending", "message": ""} for s in STAGES},
                 "instruction": None, "requirements": None, "attempts": 0, "error": None,
                 "warnings": [], "explanation": [], "busy": False, "outputs": {}}
        self._save(job_id, state)
        self.log(job_id, f"Job created for {filename}")
        return job_id

    def _save(self, job_id: str, state: dict) -> None:
        p = self.path(job_id) / "job.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)

    def load(self, job_id: str) -> dict:
        with self._locks[job_id]:
            return json.loads((self.path(job_id) / "job.json").read_text(encoding="utf-8"))

    def update(self, job_id: str, **fields: Any) -> dict:
        with self._locks[job_id]:
            s = self.load(job_id)
            s.update(fields)
            self._save(job_id, s)
            return s

    def set_stage(self, job_id: str, stage: str, status: str, message: str = "") -> None:
        with self._locks[job_id]:
            s = self.load(job_id)
            s["stages"][stage] = {"status": status, "message": message, "updated": _now()}
            if status == "running":
                s["stage"] = stage
                s["status"] = STATUS_FOR_STAGE.get(stage, s["status"])
            self._save(job_id, s)
        self.log(job_id, f"[{stage}] {status} {message}".strip())

    def reset_agent_stages(self, job_id: str) -> None:
        with self._locks[job_id]:
            s = self.load(job_id)
            for k in STAGES:
                if k != "observe" or s["stages"][k]["status"] != "done":
                    s["stages"][k] = {"status": "pending", "message": ""}
            s.update(error=None, warnings=[], explanation=[], attempts=0, outputs={})
            self._save(job_id, s)

    # ---- logging -----------------------------------------------------------
    def log(self, job_id: str, msg: str) -> None:
        with self._locks[job_id]:
            with open(self.path(job_id) / "job.log", "a", encoding="utf-8") as f:
                f.write(f"{_now()}  {msg}\n")

    def read_log(self, job_id: str, tail: int = 200) -> List[str]:
        p = self.path(job_id) / "job.log"
        return p.read_text(encoding="utf-8").splitlines()[-tail:] if p.exists() else []

    def list(self) -> List[dict]:
        out = []
        for d in sorted(self.root.iterdir(), reverse=True):
            if (d / "job.json").exists():
                s = self.load(d.name)
                out.append({k: s[k] for k in ("id", "filename", "created", "status", "instruction")})
        return out

    def source_file(self, job_id: str) -> Path:
        files = [f for f in (self.path(job_id) / "source").iterdir() if f.is_file()]
        if not files:
            raise FileNotFoundError("source video missing")
        return files[0]
