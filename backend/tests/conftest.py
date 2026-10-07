import json
import os
import subprocess
from pathlib import Path
import pytest

os.environ["TRANSCRIBER"] = "none"      # tests never download Whisper models


def make_video(path: Path, with_audio: bool = True) -> Path:
    """15s synthetic clip: 3 visually different scenes; speech-like tones with silent gaps."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=5",
           "-f", "lavfi", "-i", "smptebars=size=640x360:rate=25:duration=5",
           "-f", "lavfi", "-i", "color=c=red:size=640x360:rate=25:duration=5"]
    fc = "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]"
    if with_audio:
        # tone 0-4s, silence 4-6.5s, tone 6.5-10s, silence 10-11.5, tone 11.5-15
        segs = [("sine=f=440:d=4", 0), ("anullsrc=r=44100:cl=mono:d=2.5", 0), ("sine=f=520:d=3.5", 0),
                ("anullsrc=r=44100:cl=mono:d=1.5", 0), ("sine=f=480:d=3.5", 0)]
        for s, _ in segs:
            cmd += ["-f", "lavfi", "-i", s]
        fc += ";" + "".join(f"[{i + 3}:a]aresample=44100,aformat=channel_layouts=mono[a{i}];" for i in range(5))
        fc += "".join(f"[a{i}]" for i in range(5)) + "concat=n=5:v=0:a=1[a]"
    cmd += ["-filter_complex", fc, "-map", "[v]"] + (["-map", "[a]", "-c:a", "aac"] if with_audio else [])
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)
    return path


TRANSCRIPT = {
    "language": "en", "duration": 15.0, "segments": [
        {"start": 0.2, "end": 3.8, "text": "How to edit videos with AI today",
         "words": [{"text": "How", "start": .2, "end": .5}, {"text": "to", "start": .5, "end": .7},
                   {"text": "edit", "start": .7, "end": 1.2}, {"text": "videos", "start": 1.2, "end": 1.9},
                   {"text": "with", "start": 1.9, "end": 2.2}, {"text": "AI", "start": 2.2, "end": 2.9},
                   {"text": "today", "start": 2.9, "end": 3.8}]},
        {"start": 6.6, "end": 9.8, "text": "um the biggest tip is pricing",
         "words": [{"text": "um", "start": 6.6, "end": 7.1}, {"text": "the", "start": 7.1, "end": 7.4},
                   {"text": "biggest", "start": 7.4, "end": 8.0}, {"text": "tip", "start": 8.0, "end": 8.5},
                   {"text": "is", "start": 8.5, "end": 8.8}, {"text": "pricing", "start": 8.8, "end": 9.8}]},
        {"start": 11.6, "end": 14.8, "text": "Thanks for watching and see you",
         "words": [{"text": "Thanks", "start": 11.6, "end": 12.2}, {"text": "for", "start": 12.2, "end": 12.5},
                   {"text": "watching", "start": 12.5, "end": 13.2}, {"text": "and", "start": 13.2, "end": 13.6},
                   {"text": "see", "start": 13.6, "end": 14.1}, {"text": "you", "start": 14.1, "end": 14.8}]},
    ]}


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    return make_video(tmp_path_factory.mktemp("media") / "sample.mp4")


@pytest.fixture
def job_dir(tmp_path, sample_video) -> Path:
    """Job folder with source + a hand-made transcript (stand-in for Whisper)."""
    (tmp_path / "source").mkdir()
    (tmp_path / "analysis").mkdir()
    (tmp_path / "source" / "input.mp4").write_bytes(sample_video.read_bytes())
    (tmp_path / "analysis" / "transcript.json").write_text(json.dumps(TRANSCRIPT), encoding="utf-8")
    return tmp_path


def make_noisy_video(path: Path) -> Path:
    """6s video whose audio is only pink noise (a hissy recording)."""
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=6",
                    "-f", "lavfi", "-i", "anoisesrc=d=6:c=pink:a=0.08:r=44100",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path
