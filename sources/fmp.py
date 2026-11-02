"""Financial Modeling Prep economic calendar (API key in the FMP_API_KEY secret).

Format (from FMP's published examples): a JSON list of records with date
("YYYY-MM-DD HH:MM:SS", read as UTC), country, event, currency, previous,
estimate, actual, change, impact ("High"/"Medium"/"Low"). Two endpoints exist
(newer "stable" and older "v3"); the first one that returns events is used.
Which plan includes the calendar is FMP's choice: if the key is refused, the
source reports "unavailable" and the others carry on. The key is never printed."""
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import config as cfg
from models import Event
from sources.base import CalendarSource
from sources.common import currency_from, impact_from
from sources.http import get_json

ENDPOINTS = (
    "https://financialmodelingprep.com/stable/economic-calendar",
    "https://financialmodelingprep.com/api/v3/economic_calendar",
)
LOOKBACK_DAYS = 2
MAX_RANGE_DAYS = 90      # FMP's limit per request
# Trailing reference periods like "(Oct)" or "(Q3)" are dropped so titles line up.
_PERIOD = re.compile(
    r"\s*\((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|q[1-4])\)\s*$", re.I)


def clean_title(title):
    return _PERIOD.sub("", str(title or "").strip()).strip()


def to_event(record):
    if not isinstance(record, dict):
        return None
    currency = currency_from(record.get("currency"), record.get("country"))
    title = clean_title(record.get("event"))
    if currency is None or not title or not record.get("date"):
        return None
    try:
        return Event(source=FmpSource.source_id, title=title, country=currency,
                     time=record["date"], impact=impact_from(record.get("impact"), title),
                     previous=record.get("previous"), forecast=record.get("estimate"),
                     actual=record.get("actual"))
    except ValueError:
        return None


class FmpSource(CalendarSource):
    source_id = "fmp"

    def __init__(self, api_key=None, retries=None, timeout=None, clock=None):
        self.api_key = (api_key if api_key is not None
                        else os.environ.get("FMP_API_KEY", "")).strip()
        self.retries, self.timeout = retries, timeout
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _urls(self):
        now = self._clock()
        start = (now - timedelta(days=LOOKBACK_DAYS)).date()
        end = (now + timedelta(days=min(cfg.STORE_AHEAD_DAYS, MAX_RANGE_DAYS))).date()
        key = quote(self.api_key, safe="")
        return [f"{base}?from={start}&to={end}&apikey={key}" for base in ENDPOINTS]

    def fetch_events(self):
        if not self.api_key:
            print(f"{self.source_id}: no FMP_API_KEY set, skipping.")
            return None
        reachable = False
        for url in self._urls():
            data = get_json(url, self.source_id, self.retries, self.timeout)
            if not isinstance(data, list):
                if data is not None:
                    print(f"{self.source_id}: unexpected response: {str(data)[:100]}")
                continue
            reachable = True
            if not data:
                continue            # try the other endpoint before trusting "empty"
            events = [e for e in map(to_event, data) if e is not None]
            print(f"{self.source_id}: {len(events)} events "
                  f"({len(data) - len(events)} skipped: other economies or no time)")
            return events
        if reachable:
            print(f"{self.source_id}: no events returned.")
            return []
        return None
