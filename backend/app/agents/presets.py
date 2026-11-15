"""Platform presets: "make an Instagram Reel" should mean the right size, caption look and safe zone.

Sizes are the platforms' native upload sizes; `ideal` is a sensible length range (seconds) and `max` a
recommended upper bound (platform limits change over time - these are editing guidelines, not hard rules).
"""
from typing import Dict, List, Optional

PLATFORMS: Dict[str, dict] = {
    "reels":          {"label": "Instagram Reel", "aspect": "9:16", "size": (1080, 1920), "ideal": (15, 45), "max": 90,
                       "caption": "hormozi", "margin": 0.22},
    "shorts":         {"label": "YouTube Short", "aspect": "9:16", "size": (1080, 1920), "ideal": (15, 45), "max": 60,
                       "caption": "hormozi", "margin": 0.20},
    "tiktok":         {"label": "TikTok", "aspect": "9:16", "size": (1080, 1920), "ideal": (15, 45), "max": 60,
                       "caption": "hormozi", "margin": 0.22},
    "instagram_post": {"label": "Instagram Post", "aspect": "1:1", "size": (1080, 1080), "ideal": (10, 30), "max": 60,
                       "caption": "boxed", "margin": 0.10},
    "linkedin":       {"label": "LinkedIn", "aspect": "1:1", "size": (1080, 1080), "ideal": (20, 60), "max": 120,
                       "caption": "boxed", "margin": 0.10},
    "youtube":        {"label": "YouTube", "aspect": "16:9", "size": (1920, 1080), "ideal": None, "max": None,
                       "caption": "classic", "margin": 0.08},
}


def list_presets() -> List[dict]:
    return [{"key": k, "label": v["label"], "aspect": v["aspect"], "width": v["size"][0], "height": v["size"][1],
             "ideal": v["ideal"], "caption": v["caption"]} for k, v in PLATFORMS.items()]


def apply(req, source_duration: float) -> List[str]:
    """Fill in what the prompt left open for the chosen platform. Never overrides an explicit user choice.
    Returns human-readable notes for the explanation."""
    pf: Optional[dict] = PLATFORMS.get(req.platform or "")
    if not pf:
        return []
    notes: List[str] = []
    if req.aspect_ratio == pf["aspect"]:
        if not (req.target_width and req.target_height):
            req.target_width, req.target_height = pf["size"]
        notes.append(f"{pf['label']} preset: {pf['aspect']} at {pf['size'][0]}x{pf['size'][1]}")
    if req.caption_style == "auto":
        req.caption_style = pf["caption"]
    if req.caption_margin is None:
        req.caption_margin = pf["margin"]
    explicit_window = req.target_start is not None and req.target_end is not None
    mx, ideal = pf["max"], pf["ideal"]
    if req.target_duration is None and mx and ideal and source_duration > mx * 1.05 and not explicit_window \
            and not req.preserve_timeline:
        req.target_duration = float(ideal[1])
        req.select_mode, req.asks_edit = "best", True
        notes.append(f"{pf['label']}: your video is {source_duration:.0f}s, so the best ~{ideal[1]}s were picked "
                     f"(say a length in your prompt to change it)")
    elif req.target_duration and mx and req.target_duration > mx:
        notes.append(f"Heads-up: {req.target_duration:.0f}s is longer than the recommended {mx}s for a {pf['label']}")
    return notes
