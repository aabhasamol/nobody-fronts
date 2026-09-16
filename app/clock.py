"""A clock the demo can move by hand.

Real deployments replace this with wall time plus a scheduler; the demo needs to
show a 48-hour deadline in 48 seconds, so time is a value we set.
"""
from __future__ import annotations
from datetime import datetime, timedelta


class Clock:
    def __init__(self, start: datetime | None = None):
        self._now = start or datetime(2026, 9, 19, 10, 0)

    def now(self) -> datetime:
        return self._now

    def advance(self, hours: float) -> datetime:
        self._now += timedelta(hours=hours)
        return self._now

    def set(self, when: datetime) -> None:
        self._now = when


clock = Clock()
