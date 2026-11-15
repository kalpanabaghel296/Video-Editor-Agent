"""Face-aware reframing: keep the speaker inside the frame when a wide video becomes 9:16 / 1:1.

PLAN time (planner): sample the clip a few times per second, find the face, smooth the motion like a
virtual tripod (hold still, move only when the face really moves) and store the crop-window path in the EDL
(`edl.reframes`). RENDER time (renderer): the path becomes an ffmpeg `crop` expression of time `t`, so the
renderer needs no OpenCV. If no face is found the path is simply left out -> the old centre crop.
"""
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

SAMPLE_FPS = 4.0
MIN_FACE_RATIO = 0.25          # fewer frames with a face than this -> don't trust the tracker, centre crop
DEAD_ZONE = 0.035              # the window holds still until the face is this far (frame fraction) from its centre
MAX_SPEED = 0.30               # max window speed, fraction of the frame per second (no whip pans)

Face = Tuple[float, float, float]            # (cx, cy, size) all as 0..1 fractions of the frame
_cascades: list = []


def _load_cascades() -> list:
    if _cascades:
        return _cascades
    try:
        import cv2
        base = Path(cv2.data.haarcascades)
        for name in ("haarcascade_frontalface_default.xml", "haarcascade_frontalface_alt2.xml",
                     "haarcascade_profileface.xml"):
            p = base / name
            if p.exists():
                c = cv2.CascadeClassifier(str(p))
                if not c.empty():
                    _cascades.append(c)
    except Exception:
        pass
    return _cascades


def available() -> bool:
    """True when OpenCV and its Haar face cascades are installed."""
    try:
        import cv2  # noqa: F401
        return bool(_load_cascades())
    except Exception:
        return False


def detect_face(frame) -> Optional[Face]:
    """Largest face in a BGR frame (numpy array) or None. OpenCV Haar cascades: offline, no model download."""
    import cv2
    cs = _load_cascades()
    if not cs:
        return None
    h, w = frame.shape[:2]
    scale = 480.0 / max(w, 1) if w > 480 else 1.0
    g = cv2.cvtColor(cv2.resize(frame, (int(w * scale), int(h * scale))) if scale != 1 else frame, cv2.COLOR_BGR2GRAY)
    g = cv2.equalizeHist(g)
    gh, gw = g.shape[:2]
    best = None
    for c in cs:
        found = c.detectMultiScale(g, scaleFactor=1.1, minNeighbors=5, minSize=(int(gw * 0.06), int(gw * 0.06)))
        for (x, y, fw, fh) in found:
            if best is None or fw * fh > best[2] * best[3]:
                best = (x, y, fw, fh)
        if best is not None:
            break                                          # the first cascade that finds a face wins
    if best is None:
        return None
    x, y, fw, fh = best
    return ((x + fw / 2) / gw, (y + fh / 2) / gh, fw / gw)


def sample_faces(video: Path, t0: float, t1: float, fps: float = SAMPLE_FPS,
                 detector: Optional[Callable] = None) -> List[Tuple[float, Optional[Face]]]:
    """[(t relative to t0, Face|None)] for source seconds t0..t1."""
    import cv2
    det = detector or detect_face
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return []
    try:
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        cap.set(cv2.CAP_PROP_POS_MSEC, max(t0, 0) * 1000)
        step = 1.0 / fps
        out, next_t, t = [], 0.0, 0.0
        while t0 + t <= t1 + 1e-6:
            if not cap.grab():
                break
            if t + 1e-6 >= next_t:
                ok, frame = cap.retrieve()
                if ok:
                    out.append((round(t, 3), det(frame)))
                next_t += step
            t += 1.0 / src_fps
        return out
    finally:
        cap.release()


def _median(xs: Sequence[float], k: int = 5) -> List[float]:
    out, r = [], k // 2
    for i in range(len(xs)):
        w = sorted(xs[max(0, i - r): i + r + 1])
        out.append(w[len(w) // 2])
    return out


def _simplify(pts: List[List[float]], tol: float = 0.004) -> List[List[float]]:
    """Ramer-Douglas-Peucker on (t, c) so the ffmpeg expression stays short."""
    if len(pts) < 3:
        return pts
    (t0, c0), (t1, c1) = pts[0], pts[-1]
    worst, wi = 0.0, 0
    for i in range(1, len(pts) - 1):
        t, c = pts[i]
        lin = c0 + (c1 - c0) * (t - t0) / (t1 - t0) if t1 != t0 else c0
        d = abs(c - lin)
        if d > worst:
            worst, wi = d, i
    if worst <= tol:
        return [pts[0], pts[-1]]
    return _simplify(pts[: wi + 1], tol)[:-1] + _simplify(pts[wi:], tol)


def build_path(samples: List[Tuple[float, Optional[Face]]], duration: float, window: float,
               axis: str = "x", face_at: float = 0.5) -> Tuple[List[List[float]], float]:
    """samples -> ([[t, window_centre_fraction], ...], face_ratio).  window = size of the crop window as a
    fraction of the frame along `axis`. Returns ([], ratio) when the face tracker should not be trusted."""
    if not samples:
        return [], 0.0
    idx = 0 if axis == "x" else 1
    ratio = sum(1 for _, f in samples if f) / len(samples)
    if ratio < MIN_FACE_RATIO:
        return [], ratio
    # fill gaps: hold the last known position (first known one at the start)
    raw: List[float] = []
    last = float(next(f[idx] for _, f in samples if f))
    for _, f in samples:
        if f:
            last = float(f[idx])
        raw.append(last)
    # y axis: put the face a little above the middle of a tall window instead of in the centre
    shift = (0.5 - face_at) * window if axis == "y" else 0.0
    raw = _median(raw, 5)
    lo, hi = window / 2, 1 - window / 2
    cur = min(max(raw[0] + shift, lo), hi)
    path = [[samples[0][0], cur]]
    for (t, _), target in zip(samples[1:], raw[1:]):
        target = min(max(target + shift, lo), hi)
        dt = max(t - path[-1][0], 1e-3)
        if abs(target - cur) > DEAD_ZONE:                  # virtual tripod: move only for a real movement
            step = min(abs(target - cur) - DEAD_ZONE * 0.5, MAX_SPEED * dt)
            cur += step if target > cur else -step
        path.append([t, cur])
    if path[-1][0] < duration:
        path.append([duration, path[-1][1]])
    path = _simplify([[round(t, 3), round(c, 5)] for t, c in path])
    if len(path) == 2 and abs(path[0][1] - path[1][1]) < 1e-6:
        path = [path[0], [round(duration, 3), path[0][1]]]
    return path[:40], round(ratio, 2)


def crop_geometry(src_w: int, src_h: int, out_w: int, out_h: int) -> Tuple[Optional[str], float]:
    """Which axis moves and how big the window is (fraction of the frame) when src is cropped to the out aspect.
    -> (None, 1.0) when the aspects already match (nothing to track)."""
    a_src, a_out = src_w / max(src_h, 1), out_w / max(out_h, 1)
    if abs(a_src - a_out) / a_out < 0.03:
        return None, 1.0
    if a_src > a_out:
        return "x", a_out / a_src                          # landscape -> narrower: window is a vertical strip
    return "y", a_src / a_out                              # tall -> wider: window is a horizontal strip


def track_clip(video: Path, t0: float, t1: float, src_w: int, src_h: int, out_w: int, out_h: int,
               detector: Optional[Callable] = None) -> Optional[Tuple[str, List[List[float]], float]]:
    """-> (axis, points, face_ratio) or None (nothing to do / no trustworthy face)."""
    axis, win = crop_geometry(src_w, src_h, out_w, out_h)
    if axis is None:
        return None
    samples = sample_faces(video, t0, t1, detector=detector)
    pts, ratio = build_path(samples, t1 - t0, win, axis, face_at=0.42)
    return (axis, pts, ratio) if pts else None


def crop_filter(axis: str, points: List[List[float]], out_w: int, out_h: int) -> str:
    """ffmpeg crop filter whose window follows `points` (t is the time inside the clip, after trim+setpts)."""
    a = out_w / out_h
    lin = f"{points[-1][1]:.5f}"
    for (t0, c0), (t1, c1) in reversed(list(zip(points, points[1:]))):
        if t1 - t0 < 1e-6:
            continue
        slope = (c1 - c0) / (t1 - t0)
        lin = f"if(lt(t,{t1:.3f}),{c0:.5f}+{slope:.5f}*(t-{t0:.3f}),{lin})"
    if axis == "x":
        w, h = f"trunc(ih*{a:.6f}/2)*2", "ih"
        pos = f"x='max(0,min(iw-ow,({lin})*iw-ow/2))':y=0"
    else:
        w, h = "iw", f"trunc(iw/{a:.6f}/2)*2"
        pos = f"x=0:y='max(0,min(ih-oh,({lin})*ih-oh/2))'"
    return f"crop=w='{w}':h='{h}':{pos}"
