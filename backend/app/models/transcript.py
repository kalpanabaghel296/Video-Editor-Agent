from typing import List, Optional
from pydantic import BaseModel


class Word(BaseModel):
    text: str
    start: float
    end: float


class Segment(BaseModel):
    start: float
    end: float
    text: str
    words: List[Word] = []


class Transcript(BaseModel):
    language: Optional[str] = None
    duration: float = 0.0
    segments: List[Segment] = []
    note: Optional[str] = None      # e.g. "transcriber unavailable"
