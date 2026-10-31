"""Calendar data sources. Each source exposes fetch_events() -> list[Event] | None."""
from sources.base import CalendarSource, events_from_calendar
from sources.forex_factory import ForexFactorySource

__all__ = ["CalendarSource", "ForexFactorySource", "events_from_calendar"]
