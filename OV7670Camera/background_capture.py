"""Nonblocking five-second pixelwise background averaging."""
from array import array
from dataclasses import dataclass


@dataclass(frozen=True)
class AveragedBackground:
    width: int
    height: int
    pixels: tuple[float, ...]
    frame_count: int


class BackgroundCapture:
    duration = 5.0

    def __init__(self, width, height, started_at):
        self.width, self.height = width, height
        self.started_at, self.deadline = started_at, started_at+self.duration
        self.totals = array('d', [0.0])*(width*height)
        self.seen = set()
        self.first_at = self.last_at = None
        self.largest_gap = 0
        self.count = 0

    def add(self, frame):
        if (frame.width, frame.height) != (self.width, self.height):
            raise ValueError("Resolution changed during background capture.")
        if not self.started_at <= frame.received_at <= self.deadline:
            return
        key = (frame.sequence, frame.received_at)
        if key in self.seen:
            return
        self.seen.add(key)
        if self.last_at is not None:
            self.largest_gap = max(self.largest_gap, frame.received_at-self.last_at)
        else:
            self.first_at = frame.received_at
        self.last_at = frame.received_at
        for index, value in enumerate(frame.pixels):
            self.totals[index] += value
        self.count += 1

    def finish(self):
        if (self.count < 2 or self.first_at > self.started_at+1.5
                or self.last_at < self.deadline-1.5 or self.largest_gap > 2):
            raise ValueError("Background capture interrupted; keep the camera streaming for five seconds.")
        return AveragedBackground(self.width, self.height,
                                  tuple(value/self.count for value in self.totals), self.count)
