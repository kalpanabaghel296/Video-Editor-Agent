from typing import List
from pydantic import BaseModel
from .metadata import VideoMetadata
from .transcript import Transcript


class Scene(BaseModel):
    index: int
    start: float
    end: float
    start_frame: int
    end_frame: int


class Silence(BaseModel):
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


class Filler(BaseModel):
    word: str
    start: float
    end: float
    kind: str = "hard"          # hard = always removable (um/uh), soft = context dependent (like/actually)
    approx: bool = False        # True when timestamps were estimated (no word-level timing)


class Fumble(BaseModel):
    """A part of the recording the agent decided is a mistake and should be cut."""
    start: float
    end: float
    kind: str                   # long_pause | hesitation | stutter | false_start | retake | filler
    reason: str = ""
    confidence: float = 1.0


class AnalysisBundle(BaseModel):
    metadata: VideoMetadata
    transcript: Transcript
    scenes: List[Scene] = []
    silences: List[Silence] = []
    fillers: List[Filler] = []
    fumbles: List[Fumble] = []      # computed on load (analysis/fumbles.py), never cached