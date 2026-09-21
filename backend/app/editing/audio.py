"""
Audio design & mixing engine — background music overlay, volume normalization,
and automated sidechain audio ducking under speech.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

_ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "audio"


def get_available_music_tracks() -> dict[str, Path]:
    """
    Return available royalty-free background music tracks.

    Returns
    -------
    dict[str, Path]
        Mapping of track identifier to Path.
    """
    tracks = {}
    if _ASSETS_DIR.exists():
        for p in _ASSETS_DIR.glob("*.mp3"):
            # e.g. "chill_ambient" -> "chill", "upbeat_energetic" -> "energetic"
            key = p.stem.split("_")[0]
            tracks[key] = p
            tracks[p.stem] = p
    return tracks


def resolve_music_track(name_or_key: Optional[str]) -> Optional[Path]:
    """Resolve music track by key ('chill', 'energetic', 'upbeat') or direct path."""
    if not name_or_key:
        return None

    tracks = get_available_music_tracks()
    normalized = name_or_key.strip().lower()

    if normalized in tracks:
        return tracks[normalized]

    # Partial match
    for k, p in tracks.items():
        if normalized in k or k in normalized:
            return p

    # Direct path
    direct_p = Path(name_or_key)
    if direct_p.exists():
        return direct_p

    return None


def build_ducking_filter(
    speech_label: str,
    music_input_index: int = 1,
    music_volume: float = 0.15,
    has_speech: bool = True,
) -> Tuple[str, str]:
    """
    Generate the FFmpeg filter complex fragment for mixing background music
    with automated sidechain ducking.

    When speech is active on ``speech_label``, the music volume automatically
    compresses down; during visual pauses, the music volume smoothly rises.

    Parameters
    ----------
    speech_label : str
        The input filter label for the video's speech audio (e.g. 'outa_raw').
    music_input_index : int
        The FFmpeg input index for the music file (e.g. 1).
    music_volume : float
        Nominal volume level for background music (0.0 to 1.0, default 0.15).
    has_speech : bool
        Whether the video contains an active speech track.

    Returns
    -------
    Tuple[str, str]
        (filter_complex_fragment, output_audio_label)
    """
    vol = max(0.02, min(0.6, music_volume))
    boosted_vol = round(vol * 1.8, 3)

    if has_speech:
        # Loop music indefinitely, scale nominal volume, apply sidechain compression from speech
        filt = (
            f"[{music_input_index}:a]aloop=loop=-1:size=2e+09,volume={boosted_vol}[mloop];"
            f"[{speech_label}]asplit=2[{speech_label}_main][{speech_label}_side];"
            f"[mloop][{speech_label}_side]sidechaincompress=threshold=0.07:ratio=5:attack=40:release=350[mducked];"
            f"[{speech_label}_main][mducked]amix=inputs=2:duration=first:dropout_transition=2[outa]"
        )
    else:
        # Video has no audio: simply loop music at volume
        filt = f"[{music_input_index}:a]aloop=loop=-1:size=2e+09,volume={vol}[outa]"

    return filt, "[outa]"
