"""The one event shape every source produces and the database stores.

Kept free of any I/O and of config, so it can move to a server unchanged."""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

IMPACTS = ("high", "medium", "low", "holiday")
_EMPTY = {"", "-", "—", "n/a", "na", "null", "none"}


def _text(value):
    """Stripped text, or None when empty / a placeholder."""
    if value is None:
        return None
    value = str(value).strip()
    return None if value.lower() in _EMPTY else value


def to_utc(value):
    """Aware datetime in UTC from a datetime or an ISO string; None if invalid."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Event:
    source: str                 # which site it came from, e.g. "forex_factory"
    title: str
    country: str                # currency code, e.g. "USD"
    time: datetime              # aware, UTC
    impact: str = ""            # one of IMPACTS, or "" when the source gives none
    previous: Optional[str] = None
    forecast: Optional[str] = None
    actual: Optional[str] = None

    def __post_init__(self):
        time = to_utc(self.time)
        if time is None:
            raise ValueError(f"invalid event time: {self.time!r}")
        impact = (_text(self.impact) or "").lower()
        set_ = object.__setattr__
        set_(self, "time", time)
        set_(self, "source", _text(self.source) or "unknown")
        set_(self, "title", _text(self.title) or "Unknown event")
        set_(self, "country", _text(self.country) or "N/A")
        set_(self, "impact", impact if impact in IMPACTS else "")
        for field in ("previous", "forecast", "actual"):
            set_(self, field, _text(getattr(self, field)))

    @property
    def key(self):
        """Same shape as the alert state keys: 'country|title|ISO-time'."""
        return f"{self.country}|{self.title}|{self.time.isoformat()}"

    def to_calendar_dict(self):
        """The dict shape the alert and summary code already reads."""
        return {
            "title": self.title,
            "country": self.country,
            "impact": self.impact.capitalize(),
            "date": self.time.isoformat(),
            "forecast": self.forecast or "",
            "previous": self.previous or "",
            "actual": self.actual or "",
        }

    @classmethod
    def from_calendar_dict(cls, data, source):
        """Build from a feed-style dict; None if it has no usable time."""
        if not isinstance(data, dict) or to_utc(data.get("date")) is None:
            return None
        return cls(source=source, title=data.get("title"),
                   country=data.get("country"), time=data.get("date"),
                   impact=data.get("impact"), previous=data.get("previous"),
                   forecast=data.get("forecast"), actual=data.get("actual"))
