"""biquote.io economic calendar (free, no API key).

GET https://biquote.io/api/calendar?from=YYYY-MM-DD&to=YYYY-MM-DD
Record: eventId, time (UTC, ISO with Z), countryCode, currency, name,
importance (low/medium/high), type (event/indicator/holiday), unit, multiplier,
actual, forecast, previous, timeMode ("exact" when the time is real).

Used as a gap-filler: calendar_service lists it in GAP_FILL_SOURCES, so its own
events only appear on days the other sources have nothing for. Events whose time
is only a placeholder are skipped, so no alert ever fires at a made-up hour."""
import os
import time
from datetime import datetime, timedelta, timezone

import config as cfg
from models import Event
from sources.base import CalendarSource
from sources.common import currency_from, impact_from, value_text
from sources.http import get_json

URL = "https://biquote.io/api/calendar"
LOOKBACK_DAYS = 2
# The service returns at most 200 records per request, oldest first. One request for
# weeks at a time therefore only ever covered the first day or two, so it is asked
# one day at a time (each request: that day to the next). Days further out than
# AHEAD_DAYS are left out to keep the number of requests small.
RESPONSE_CAP = 200
try:
    AHEAD_DAYS = min(60, max(1, int(os.environ.get("BIQUOTE_AHEAD_DAYS", "14"))))
except ValueError:
    AHEAD_DAYS = 14
PAUSE_SECONDS = 0.2        # between requests, to be polite to a free service
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

    def __init__(self, retries=None, timeout=None, clock=None, pause=PAUSE_SECONDS):
        self.retries, self.timeout, self.pause = retries, timeout, pause
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _urls(self):
        """One request per day, from a couple of days back to AHEAD_DAYS ahead."""
        first = (self._clock() - timedelta(days=LOOKBACK_DAYS)).date()
        days = LOOKBACK_DAYS + 1 + min(cfg.STORE_AHEAD_DAYS, AHEAD_DAYS)
        return [f"{URL}?from={first + timedelta(days=n)}&to={first + timedelta(days=n + 1)}"
                for n in range(days)]

    def fetch_events(self):
        unique, seen, full_days = [], set(), 0
        for number, url in enumerate(self._urls()):
            if number and self.pause:
                time.sleep(self.pause)
            data = get_json(url, self.source_id, self.retries, self.timeout)
            records = _records(data) if data is not None else None
            if records is None:
                if data is not None:
                    print(f"{self.source_id}: unexpected response: {str(data)[:100]}")
                if not unique and number == 0:
                    return None                  # nothing at all: the source is down
                print(f"{self.source_id}: stopped early; later days come next time")
                break
            full_days += len(records) >= RESPONSE_CAP
            for record in records:
                key = (tuple(str(record.get(f)) for f in
                             ("eventId", "id", "time", "currency", "name", "timeMode"))
                       if isinstance(record, dict) else repr(record))
                if key not in seen:
                    seen.add(key)
                    unique.append(record)
        events = [e for e in map(to_event, unique) if e is not None]
        print(f"{self.source_id}: {len(events)} events "
              f"({len(unique) - len(events)} skipped: other economies or no exact time)")
        if full_days:
            print(f"{self.source_id}: {full_days} day(s) hit the {RESPONSE_CAP}-record limit")
        return events
