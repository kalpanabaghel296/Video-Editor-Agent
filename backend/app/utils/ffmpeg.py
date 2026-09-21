"""
FFmpeg and FFprobe binary path resolution utility.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
from typing import Optional


def _get_windows_registry_path() -> str:
    """Retrieve combined User and Machine PATH environment strings from Windows registry."""
    if os.name != "nt":
        return ""
    try:
        import winreg

        def _read_reg(root, subkey):
            try:
                with winreg.OpenKey(root, subkey) as key:
                    val, _ = winreg.QueryValueEx(key, "Path")
                    return val or ""
            except OSError:
                return ""

        user_path = _read_reg(winreg.HKEY_CURRENT_USER, r"Environment")
        machine_path = _read_reg(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        )
        return f"{user_path};{machine_path}"
    except Exception:
        return ""


def get_ffprobe_binary() -> str:
    """
    Resolve the ffprobe binary path.

    Resolution order:
    1. FFPROBE_PATH environment variable
    2. shutil.which("ffprobe")
    3. Windows registry PATH search
    4. Sibling directory of ffmpeg (if ffmpeg is located)
    5. imageio-ffmpeg directory
    """
    env_fp = os.getenv("FFPROBE_PATH")
    if env_fp and Path(env_fp).is_file():
        return env_fp

    sys_ffprobe = shutil.which("ffprobe")
    if sys_ffprobe:
        return sys_ffprobe

    # Sibling of ffmpeg on PATH
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        sibling = Path(sys_ffmpeg).parent / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if sibling.is_file():
            return str(sibling)

    # Windows registry search
    reg_path = _get_windows_registry_path()
    if reg_path:
        cand = shutil.which("ffprobe", path=reg_path)
        if cand:
            return cand
        cand_ffmpeg = shutil.which("ffmpeg", path=reg_path)
        if cand_ffmpeg:
            sibling = Path(cand_ffmpeg).parent / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
            if sibling.is_file():
                return str(sibling)

    # imageio-ffmpeg check
    try:
        import imageio_ffmpeg
        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled:
            sibling = Path(bundled).parent / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
            if sibling.is_file():
                return str(sibling)
    except Exception:
        pass

    raise FileNotFoundError(
        "ffprobe was not found. Please ensure FFmpeg/FFprobe is installed and available "
        "in your PATH, or set the FFPROBE_PATH environment variable."
    )


def get_ffmpeg_binary() -> str:
    """
    Resolve the ffmpeg binary path.

    Resolution order:
    1. FFMPEG_PATH environment variable
    2. shutil.which("ffmpeg")
    3. Sibling directory of ffprobe (if ffprobe is located)
    4. Windows registry PATH search
    5. imageio-ffmpeg bundled binary
    """
    env_fm = os.getenv("FFMPEG_PATH")
    if env_fm and Path(env_fm).is_file():
        return env_fm

    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg

    # Sibling of ffprobe on PATH
    sys_ffprobe = shutil.which("ffprobe")
    if sys_ffprobe:
        sibling = Path(sys_ffprobe).parent / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if sibling.is_file():
            return str(sibling)

    # Windows registry search
    reg_path = _get_windows_registry_path()
    if reg_path:
        cand = shutil.which("ffmpeg", path=reg_path)
        if cand:
            return cand
        cand_ffprobe = shutil.which("ffprobe", path=reg_path)
        if cand_ffprobe:
            sibling = Path(cand_ffprobe).parent / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            if sibling.is_file():
                return str(sibling)

    # imageio-ffmpeg bundled binary
    try:
        import imageio_ffmpeg
        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and Path(bundled).is_file():
            return bundled
    except Exception:
        pass

    raise FileNotFoundError(
        "ffmpeg was not found. Please ensure FFmpeg is installed and available "
        "in your PATH, or set the FFMPEG_PATH environment variable."
    )
