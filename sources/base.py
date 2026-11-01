"""What every calendar source must offer, so sources can be added or swapped
without touching the rest of the bot."""
from abc import ABC, abstractmethod

from models import Event


class CalendarSource(ABC):
    source_id = "unknown"   # short stable name stored with every event, e.g. "fmp"

    @abstractmethod
    def fetch_events(self):
        """Return a list of Event objects, or None if the source could not be
        reached (so 'no data' is never confused with 'no events')."""


def events_from_calendar(raw, source_id):
    """Convert feed-style dicts to Events. Returns (events, skipped_count);
    entries without a usable time are skipped, never fatal."""
    events, skipped = [], 0
    for item in raw:
        event = Event.from_calendar_dict(item, source_id)
        if event is None:
            skipped += 1
        else:
            events.append(event)
    return events, skipped
