"""Finnhub economic calendar (API key in the FINNHUB_API_KEY secret).

Format: {"economicCalendar": [ {actual, country, estimate, event, impact, prev,
time, unit}, ... ]} where the numbers are floats and `unit` says what they are
(%, K, ...). `time` is read as UTC. Finnhub may restrict the calendar to paid
plans: if the key is refused, the source reports "unavailable" and the others
carry on. The key is sent in a header, so it is never part of the URL either."""
import os
from datetime import datetime, timedelta, timezone

import config as cfg
from models import Event, to_utc
from sources.base import CalendarSource
from sources.common import currency_from, impact_from, value_text
from sources.http import get_json

URL = "https://finnhub.io/api/v1/calendar/economic"
LOOKBACK_DAYS = 2


def to_event(record):
    if not isinstance(record, dict):
        return None
    currency = currency_from(None, record.get("country"))
    title = str(record.get("event") or "").strip()
    if currency is None or not title or not record.get("time"):
        return None
    unit = record.get("unit")
    try:
        return Event(source=FinnhubSource.source_id, title=title, country=currency,
                     time=record["time"], impact=impact_from(record.get("impact"), title),
                     previous=value_text(record.get("prev"), unit),
                     forecast=value_text(record.get("estimate"), unit),
                     actual=value_text(record.get("actual"), unit))
    except ValueError:
        return None


class FinnhubSource(CalendarSource):
    source_id = "finnhub"

    def __init__(self, api_key=None, retries=None, timeout=None, clock=None):
        self.api_key = (api_key if api_key is not None
                        else os.environ.get("FINNHUB_API_KEY", "")).strip()
        self.retries, self.timeout = retries, timeout
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def fetch_events(self):
        if not self.api_key:
            print(f"{self.source_id}: no FINNHUB_API_KEY set, skipping.")
            return None
        data = get_json(URL, self.source_id, self.retries, self.timeout,
                        headers={"X-Finnhub-Token": self.api_key})
        if data is None:
            return None
        records = data.get("economicCalendar") if isinstance(data, dict) else data
        if not isinstance(records, list):
            print(f"{self.source_id}: unexpected response: {str(data)[:100]}")
            return None
        now = self._clock()
        start = now - timedelta(days=LOOKBACK_DAYS)
        end = now + timedelta(days=cfg.STORE_AHEAD_DAYS)
        events = []
        for event in map(to_event, records):
            if event is not None and start <= to_utc(event.time) <= end:
                events.append(event)
        print(f"{self.source_id}: {len(events)} events "
              f"({len(records) - len(events)} skipped: other economies, no time or out of range)")
        return events
