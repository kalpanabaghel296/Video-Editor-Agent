"""Thin, safe FFmpeg/ffprobe wrappers.

Rule from the plan: no raw shell strings, and nothing coming from an LLM is ever
executed. Commands are always built as argument lists by our own code.
"""
import json
import subprocess
from pathlib import Path
from typing import Optional, Sequence


class FFmpegError(RuntimeError):
    def __init__(self, cmd: Sequence[str], stderr: str):
        self.cmd = list(cmd)
        self.stderr = stderr
        super().__init__(f"{cmd[0]} failed: {stderr.strip()[-600:]}")


def run(cmd: Sequence[str], cwd: Optional[Path] = None, timeout: int = 3600) -> subprocess.CompletedProcess:
    p = subprocess.run(list(map(str, cmd)), capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       cwd=str(cwd) if cwd else None, timeout=timeout)
    if p.returncode != 0:
        raise FFmpegError(cmd, p.stderr)
    return p


def ffprobe_json(path: Path) -> dict:
    p = run(["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)])
    return json.loads(p.stdout or "{}")
