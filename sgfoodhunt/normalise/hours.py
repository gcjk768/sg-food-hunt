"""Opening hours normalisation into {day: [[open, close], ...]} plus weekend / late night flags."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_ALIASES = {
    "monday": "mon",
    "mon": "mon",
    "tuesday": "tue",
    "tue": "tue",
    "tues": "tue",
    "wednesday": "wed",
    "wed": "wed",
    "thursday": "thu",
    "thu": "thu",
    "thur": "thu",
    "thurs": "thu",
    "friday": "fri",
    "fri": "fri",
    "saturday": "sat",
    "sat": "sat",
    "sunday": "sun",
    "sun": "sun",
    "daily": "daily",
    "everyday": "daily",
    "every day": "daily",
    "weekends": "weekend",
    "weekend": "weekend",
    "weekdays": "weekday",
    "weekday": "weekday",
    "ph": "ph",
    "public holidays": "ph",
    "public holiday": "ph",
}
TIME_RE = re.compile(r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?", re.IGNORECASE)
RANGE_RE = re.compile(
    r"(\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm)?)\s*(?:-|–|—|to|~)\s*(\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm)?)",
    re.IGNORECASE,
)
_DAY_WORDS = (
    "mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun|monday|tuesday|wednesday|thursday|friday"
    "|saturday|sunday"
)
DAY_SPAN_RE = re.compile(
    rf"\b({_DAY_WORDS}|daily|everyday|every day|weekends|weekend|weekdays|weekday"
    rf"|ph|public holidays?)\b(?:\s*(?:-|–|—|to)\s*({_DAY_WORDS}))?",
    re.IGNORECASE,
)
CLOSED_RE = re.compile(r"\bclosed\b", re.IGNORECASE)


@dataclass(slots=True)
class HoursInfo:
    hours: dict[str, list[list[str]]] = field(default_factory=dict)
    open_weekends: bool | None = None
    late_night: bool | None = None
    ph_closed: bool | None = None
    source: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "hours": self.hours,
            "open_weekends": self.open_weekends,
            "late_night": self.late_night,
            "ph_closed": self.ph_closed,
        }


def _to_24h(text: str, assume_pm_after: int = 6) -> str | None:
    m = TIME_RE.match(text.strip())
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if hour > 24 or minute > 59:
        return None
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    elif not ampm and hour <= assume_pm_after and hour != 0:
        hour += 12  # "5 - 10" style text without am/pm: afternoon / evening
    return f"{hour % 24:02d}:{minute:02d}"


def _parse_range(first: str, second: str, assume_pm_after: int) -> list[str] | None:
    """Parse "5:00 - 11:30 PM" style ranges.

    A missing am/pm on the first time borrows the second's.
    """
    has_ampm = TIME_RE.match(first.strip())
    second_m = TIME_RE.match(second.strip())
    if has_ampm and second_m and not has_ampm.group(3) and second_m.group(3):
        first = f"{first.strip()} {second_m.group(3)}"
        a, b = _to_24h(first, assume_pm_after=0), _to_24h(second, assume_pm_after=0)
        if a and b and a >= b:  # "11 - 2pm" means 11am - 2pm
            a = _to_24h(first.rsplit(" ", 1)[0], assume_pm_after=0)
    else:
        a, b = _to_24h(first, assume_pm_after), _to_24h(second, assume_pm_after)
    return [a, b] if a and b else None


def _expand_days(first: str, last: str | None) -> list[str]:
    a = DAY_ALIASES.get(first.lower())
    if a == "daily":
        return list(DAYS)
    if a == "weekend":
        return ["sat", "sun"]
    if a == "weekday":
        return list(DAYS[:5])
    if a == "ph":
        return ["ph"]
    if a is None:
        return []
    if not last:
        return [a]
    b = DAY_ALIASES.get(last.lower())
    if b is None or b not in DAYS:
        return [a]
    i, j = DAYS.index(a), DAYS.index(b)
    return list(DAYS[i : j + 1]) if i <= j else list(DAYS[i:]) + list(DAYS[: j + 1])


def _finalise(info: HoursInfo) -> HoursInfo:
    if not info.hours:
        return info
    days = {d for d in info.hours if d in DAYS}
    if days:
        info.open_weekends = bool(info.hours.get("sat") or info.hours.get("sun"))
        closes = [rng[1] for d in DAYS for rng in info.hours.get(d, [])]
        info.late_night = any(c >= "23:00" or c < "05:00" for c in closes) if closes else None
    if "ph" in info.hours:
        info.ph_closed = not info.hours["ph"]
    return info


def parse_google_weekday_descriptions(lines: list[str]) -> HoursInfo:
    """``["Monday: 7:30 AM - 8:00 PM", "Tuesday: Closed", ...]`` from the Places API."""
    info = HoursInfo(source="google")
    for line in lines:
        day_text, _, rest = line.partition(":")
        day = DAY_ALIASES.get(day_text.strip().lower())
        if day not in DAYS:
            continue
        rest = rest.replace(" ", " ").replace(" ", " ")
        if CLOSED_RE.search(rest):
            info.hours[day] = []
            continue
        ranges = []
        for m in RANGE_RE.finditer(rest):
            rng = _parse_range(m.group(1), m.group(2), assume_pm_after=0)
            if rng:
                ranges.append(rng)
        if "open 24 hours" in rest.lower():
            ranges = [["00:00", "24:00"]]
        info.hours[day] = ranges
    return _finalise(info)


def parse_hours_text(text: str) -> HoursInfo:
    """Best effort parse of free text.

    Example: ``"Mon-Fri 11.30am-2.30pm, 5-10pm; Sat-Sun 11am-10pm; Closed on PH"``.
    """
    info = HoursInfo(source="text")
    segments = re.split(r"[;,\n]|\s{2,}|\.\s+(?=[A-Z])", text)
    current_days: list[str] = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        dm = DAY_SPAN_RE.search(seg)
        if dm:
            current_days = _expand_days(dm.group(1), dm.group(2))
        if not current_days:
            if RANGE_RE.search(seg):
                current_days = list(DAYS)  # ranges with no day word: assume daily
            else:
                continue
        if CLOSED_RE.search(seg) and not RANGE_RE.search(seg):
            for d in current_days:
                info.hours[d] = []
            continue
        for m in RANGE_RE.finditer(seg):
            rng = _parse_range(m.group(1), m.group(2), assume_pm_after=6)
            if rng:
                for d in current_days:
                    info.hours.setdefault(d, []).append(list(rng))
    return _finalise(info)


def normalise_hours(raw: dict[str, Any] | None) -> HoursInfo | None:
    """Accept the source native shapes stored in raw candidates."""
    if not raw:
        return None
    lines = raw.get("weekday_descriptions")
    if isinstance(lines, list) and lines:
        return parse_google_weekday_descriptions([str(x) for x in lines])
    text = raw.get("text")
    if isinstance(text, str) and text.strip():
        info = parse_hours_text(text)
        return info if info.hours else None
    return None
