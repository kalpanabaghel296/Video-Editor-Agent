"""Thin, safe FFmpeg/ffprobe wrappers.

Rule from the plan: no raw shell strings, and nothing coming from an LLM is ever
executed. Commands are always built as argument lists by our own code.

Week 7: the binaries are looked up in a robust way (FFMPEG_PATH -> PATH -> the places winget / common
installers use on Windows), so a server that was started before FFmpeg was installed - or with a stale
PATH - still finds it. A missing binary raises FFmpegMissing (clear message) instead of a raw WinError 2.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from .. import config

_NAMES = ("ffmpeg", "ffprobe")
_cache: dict = {}


class FFmpegError(RuntimeError):
    def __init__(self, cmd: Sequence[str], stderr: str):
        self.cmd = list(cmd)
        self.stderr = stderr
        super().__init__(f"{cmd[0]} failed: {stderr.strip()[-600:]}")


class FFmpegMissing(FFmpegError):
    """ffmpeg / ffprobe could not be found or started."""
    def __init__(self, name: str, detail: str = ""):
        self.name = name
        self.cmd = [name]
        self.stderr = detail
        RuntimeError.__init__(self, install_hint(name, detail))


def install_hint(name: str = "ffmpeg", detail: str = "") -> str:
    base = f"{name} nahi mila (not found)."
    if sys.platform == "win32":
        how = ("Install karo:  winget install Gyan.FFmpeg   — phir backend server restart karo "
               "(ya FFMPEG_PATH me ffmpeg ka bin folder do).")
    else:
        how = "Install karo (apt install ffmpeg / brew install ffmpeg) ya FFMPEG_PATH set karo."
    return f"{base} {how}" + (f" [{detail}]" if detail else "")


def _windows_extra_dirs() -> List[Path]:
    """Folders where Windows installers put ffmpeg, in case this process has a stale PATH."""
    dirs: List[Path] = []
    try:                                                   # PATH as it is NOW in the registry (not as at process start)
        import winreg                                      # type: ignore
        for hive, sub in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                          (winreg.HKEY_CURRENT_USER, "Environment")):
            try:
                with winreg.OpenKey(hive, sub) as k:
                    val, _ = winreg.QueryValueEx(k, "Path")
                    dirs += [Path(os.path.expandvars(p)) for p in str(val).split(";") if p.strip()]
            except OSError:
                pass
    except Exception:
        pass
    local = os.environ.get("LOCALAPPDATA")
    if local:
        base = Path(local) / "Microsoft" / "WinGet"
        dirs.append(base / "Links")
        pk = base / "Packages"
        if pk.exists():
            dirs += [d for p in pk.glob("*FFmpeg*") for d in p.glob("**/bin") if d.is_dir()]
    for d in (r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin", r"C:\ProgramData\chocolatey\bin"):
        dirs.append(Path(d))
    return dirs


def find_binary(name: str) -> Optional[str]:
    """Full path of `name` (ffmpeg | ffprobe) or None. Positive results are cached."""
    if name in _cache and Path(_cache[name]).exists():
        return _cache[name]
    exe = name + (".exe" if sys.platform == "win32" else "")
    cands: List[Optional[str]] = []
    fp = config.FFMPEG_PATH.strip().strip('"')
    if fp:
        p = Path(fp)
        if p.is_file():                                    # FFMPEG_PATH pointing at ffmpeg itself -> sibling ffprobe
            cands.append(str(p.with_name(exe)) if p.name.lower().startswith(("ffmpeg", "ffprobe")) else str(p))
        else:
            cands += [str(p / exe), str(p / "bin" / exe)]
    cands.append(shutil.which(name))
    if sys.platform == "win32":
        cands += [str(d / exe) for d in _windows_extra_dirs()]
    for c in cands:
        if c and Path(c).is_file():
            _cache[name] = c
            return c
    return None


def reset_cache() -> None:
    _cache.clear()


def ffmpeg_status() -> dict:
    """For /api/health and the UI banner."""
    found = {n: find_binary(n) for n in _NAMES}
    version = None
    if found["ffmpeg"]:
        try:
            version = subprocess.run([found["ffmpeg"], "-version"], capture_output=True, text=True, timeout=10,
                                     encoding="utf-8", errors="replace").stdout.splitlines()[0]
        except Exception:
            version = None
    missing = [n for n, p in found.items() if not p]
    return {"ok": not missing, "ffmpeg": found["ffmpeg"], "ffprobe": found["ffprobe"], "version": version,
            "message": "" if not missing else install_hint(missing[0])}


def _resolve(cmd: Sequence[str]) -> List[str]:
    out = [str(c) for c in cmd]
    if out and out[0] in _NAMES:
        p = find_binary(out[0])
        if p is None:
            raise FFmpegMissing(out[0])
        out[0] = p
    return out


def run(cmd: Sequence[str], cwd: Optional[Path] = None, timeout: int = 3600) -> subprocess.CompletedProcess:
    full = _resolve(cmd)
    try:
        p = subprocess.run(full, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           cwd=str(cwd) if cwd else None, timeout=timeout)
    except FileNotFoundError as e:                         # [WinError 2] and friends
        raise FFmpegMissing(str(cmd[0]), str(e))
    except PermissionError as e:                           # e.g. blocked by an Application Control policy
        raise FFmpegMissing(str(cmd[0]), f"blocked/permission: {e}")
    if p.returncode != 0:
        raise FFmpegError(cmd, p.stderr)
    return p


def run_bytes(cmd: Sequence[str], timeout: int = 600) -> bytes:
    """Like run() but returns raw stdout (e.g. PCM audio piped out of ffmpeg)."""
    full = _resolve(cmd)
    try:
        p = subprocess.run(full, capture_output=True, timeout=timeout)
    except (FileNotFoundError, PermissionError) as e:
        raise FFmpegMissing(str(cmd[0]), str(e))
    if p.returncode != 0:
        raise FFmpegError(cmd, p.stderr.decode("utf-8", "replace"))
    return p.stdout


def ffprobe_json(path: Path) -> dict:
    p = run(["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)])
    return json.loads(p.stdout or "{}")
