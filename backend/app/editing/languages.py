"""Caption languages: the 7 languages offered in the UI, their script and the font that can draw them.

To add a language, add ONE entry to LANGUAGES (code = Whisper / ISO-639-1 code, script = key of SCRIPT_RANGE / FONTS).
"""
import platform
from typing import Dict, List, Optional

# code, English name, name in its own script, script
LANGUAGES: List[Dict[str, str]] = [
    {"code": "en", "name": "English", "native": "English", "script": "latin"},
    {"code": "hi", "name": "Hindi", "native": "हिन्दी", "script": "devanagari"},
    {"code": "kn", "name": "Kannada", "native": "ಕನ್ನಡ", "script": "kannada"},
    {"code": "ta", "name": "Tamil", "native": "தமிழ்", "script": "tamil"},
    {"code": "te", "name": "Telugu", "native": "తెలుగు", "script": "telugu"},
    {"code": "bn", "name": "Bengali", "native": "বাংলা", "script": "bengali"},
    {"code": "es", "name": "Spanish", "native": "Español", "script": "latin"},
]
BY_CODE = {l["code"]: l for l in LANGUAGES}

SCRIPT_RANGE = {                      # unicode blocks
    "devanagari": (0x0900, 0x097F), "bengali": (0x0980, 0x09FF), "tamil": (0x0B80, 0x0BFF),
    "telugu": (0x0C00, 0x0C7F), "kannada": (0x0C80, 0x0CFF),
}

# font that has the glyphs, per OS. Windows' "Nirmala UI" covers every Indic script above and ships with Windows.
FONTS = {
    "Windows": {s: "Nirmala UI" for s in SCRIPT_RANGE},
    "Darwin": {"devanagari": "Kohinoor Devanagari", "bengali": "Bangla Sangam MN", "tamil": "Tamil Sangam MN",
               "telugu": "Telugu Sangam MN", "kannada": "Kannada Sangam MN"},
    "Linux": {"devanagari": "Noto Sans Devanagari", "bengali": "Noto Sans Bengali", "tamil": "Noto Sans Tamil",
              "telugu": "Noto Sans Telugu", "kannada": "Noto Sans Kannada"},
}


def normalize(code: Optional[str]) -> Optional[str]:
    """'auto' / '' / None / unknown -> None (= captions as spoken). 'hi-IN' -> 'hi'."""
    if not code:
        return None
    c = str(code).strip().lower().replace("_", "-").split("-")[0]
    return c if c in BY_CODE else None


def script_of(code: Optional[str]) -> str:
    return BY_CODE.get(code or "", {}).get("script", "latin")


def has_script(text: str, script: str) -> bool:
    r = SCRIPT_RANGE.get(script)
    return bool(r) and any(r[0] <= ord(ch) <= r[1] for ch in text)


def detect_script(text: str) -> Optional[str]:
    """Which Indic script is this text written in (None = Latin / unknown)? The most frequent one wins."""
    counts = {s: sum(1 for ch in text if r[0] <= ord(ch) <= r[1]) for s, r in SCRIPT_RANGE.items()}
    best = max(counts, key=counts.get)
    return best if counts[best] else None


def font_for_script(script: Optional[str]) -> Optional[str]:
    if not script or script == "latin":
        return None
    table = FONTS.get(platform.system()) or FONTS["Linux"]
    return table.get(script)