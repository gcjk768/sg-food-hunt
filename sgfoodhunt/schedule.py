"""A tiny in-process scheduler so the Docker container needs no cron.

``SGFH_SCHEDULE`` accepts ``"mon 03:17"`` (weekly), ``"daily 03:17"`` or a comma separated list of
days such as ``"mon,thu 03:17"``. Times are local to the container's TZ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_RE = re.compile(r"^\s*([a-z,]+)\s+(\d{1,2}):(\d{2})\s*$", re.IGNORECASE)


@dataclass(slots=True, frozen=True)
class Schedule:
    weekdays: tuple[int, ...]  # 0 = Monday
    hour: int
    minute: int

    def next_after(self, now: datetime) -> datetime:
        """The next scheduled datetime strictly after ``now`` (same tzinfo as ``now``)."""
        for offset in range(0, 8):
            day = (now + timedelta(days=offset)).replace(
                hour=self.hour, minute=self.minute, second=0, microsecond=0
            )
            if day.weekday() in self.weekdays and day > now:
                return day
        raise RuntimeError("no run day within a week")  # pragma: no cover

    def describe(self) -> str:
        days = "daily" if len(self.weekdays) == 7 else ",".join(DAYS[d] for d in self.weekdays)
        return f"{days} {self.hour:02d}:{self.minute:02d}"


def parse_schedule(text: str) -> Schedule:
    match = _RE.match(text or "")
    if not match:
        raise ValueError(
            f"bad schedule {text!r}: use e.g. 'mon 03:17', 'daily 03:17' or 'mon,thu 03:17'"
        )
    days_text, hour, minute = match.group(1).lower(), int(match.group(2)), int(match.group(3))
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"bad time in schedule {text!r}")
    if days_text in ("daily", "everyday", "*"):
        weekdays: tuple[int, ...] = tuple(range(7))
    else:
        parsed = []
        for d in days_text.split(","):
            d = d.strip()[:3]
            if d not in DAYS:
                raise ValueError(f"unknown day {d!r} in schedule {text!r}")
            parsed.append(DAYS.index(d))
        weekdays = tuple(sorted(set(parsed)))
    return Schedule(weekdays=weekdays, hour=hour, minute=minute)
