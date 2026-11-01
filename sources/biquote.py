"""biquote.io economic calendar (free, no API key).

GET https://biquote.io/api/calendar?from=YYYY-MM-DD&to=YYYY-MM-DD
Record: eventId, time (UTC, ISO with Z), countryCode, currency, name,
importance (low/medium/high), type (event/indicator/holiday), unit, multiplier,
actual, forecast, previous, timeMode ("exact" when the time is real).

Used as a gap-filler: calendar_service lists it in GAP_FILL_SOURCES, so its own
events only appear on days the other sources have nothing for. Events whose time
is only a placeholder are skipped, so no alert ever fires at a made-up hour."""
from datetime import datetime, timedelta, timezone

import config as cfg
from models import Event
from sources.base import CalendarSource
from sources.common import currency_from, impact_from, value_text
from sources.http import get_json

URL = "https://biquote.io/api/calendar"
LOOKBACK_DAYS = 2
MAX_RANGE_DAYS = 90        # the service keeps about 90 days either side
_MULTIPLIER = {"thousand": "K", "thousands": "K", "million": "M", "millions": "M",
               "billion": "B", "billions": "B", "trillion": "T", "trillions": "T"}
_PERCENT = {"percent", "percentage", "%", "pct"}


def _suffix(unit, multiplier):
    if str(unit or "").strip().lower() in _PERCENT:
        return "%"
    return _MULTIPLIER.get(str(multiplier or "").strip().lower(), "")


def to_event(record):
    """One API record -> Event, or None (other economy, no real time, unreadable)."""
    if not isinstance(record, dict) or record.get("timeMode", "exact") != "exact":
        return None
    currency = currency_from(record.get("currency"), record.get("countryCode"))
    title = str(record.get("name") or "").strip()
    if currency is None or not title or not record.get("time"):
        return None
    impact = ("holiday" if str(record.get("type") or "").lower() == "holiday"
              else impact_from(record.get("importance"), title))
    suffix = _suffix(record.get("unit"), record.get("multiplier"))
    try:
        return Event(source=BiquoteSource.source_id, title=title, country=currency,
                     time=record["time"], impact=impact,
                     previous=value_text(record.get("previous"), suffix),
                     forecast=value_text(record.get("forecast"), suffix),
                     actual=value_text(record.get("actual"), suffix))
    except ValueError:
        return None


def _records(data):
    """The list of records, whether the response is a list or wraps one."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("events", "data", "items", "results"):
            if isinstance(data.get(key), list):
                return data[key]
    return None


class BiquoteSource(CalendarSource):
    source_id = "biquote"

    def __init__(self, retries=None, timeout=None, clock=None):
        self.retries, self.timeout = retries, timeout
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _url(self):
        now = self._clock()
        start = (now - timedelta(days=LOOKBACK_DAYS)).date()
        end = (now + timedelta(days=min(cfg.HORIZON_DAYS, MAX_RANGE_DAYS))).date()
        return f"{URL}?from={start}&to={end}"

    def fetch_events(self):
        data = get_json(self._url(), self.source_id, self.retries, self.timeout)
        if data is None:
            return None
        records = _records(data)
        if records is None:
            print(f"{self.source_id}: unexpected response: {str(data)[:100]}")
            return None
        events = [e for e in map(to_event, records) if e is not None]
        print(f"{self.source_id}: {len(events)} events "
              f"({len(records) - len(events)} skipped: other economies or no exact time)")
        return events
