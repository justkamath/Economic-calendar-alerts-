"""Trading Economics economic calendar (official API).

Needs an API key in the TRADING_ECONOMICS_KEY secret; without one this source
simply reports "unavailable" and the others carry on. The key is never printed.

Their format (docs.tradingeconomics.com/economic_calendar/snapshot):
  GET https://api.tradingeconomics.com/calendar/country/All/{from}/{to}?c=KEY&f=json
  Date is UTC without an offset, Country is a full name ("Australia"), Currency is
  often empty, Importance is 1 (low) / 2 (medium) / 3 (high), Forecast is the
  economists' consensus (TEForecast is their own estimate and is not used)."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import config as cfg
from models import Event
from sources.base import CalendarSource

BASE_URL = "https://api.tradingeconomics.com/calendar/country/All"
LOOKBACK_DAYS = 2
IMPACT = {1: "low", 2: "medium", 3: "high"}
KNOWN_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "CNY"}
# Only the economies Forex Factory covers, so the sources line up.
CURRENCY_BY_COUNTRY = {
    "united states": "USD",
    "euro area": "EUR", "germany": "EUR", "france": "EUR", "italy": "EUR", "spain": "EUR",
    "united kingdom": "GBP",
    "japan": "JPY",
    "australia": "AUD",
    "new zealand": "NZD",
    "canada": "CAD",
    "switzerland": "CHF",
    "china": "CNY",
}


def _currency(record):
    code = str(record.get("Currency") or "").strip().upper()
    if code in KNOWN_CURRENCIES:
        return code
    return CURRENCY_BY_COUNTRY.get(str(record.get("Country") or "").strip().lower())


def to_event(record):
    """One API record -> Event, or None if it is not for a covered economy / has no time."""
    if not isinstance(record, dict):
        return None
    currency = _currency(record)
    title = str(record.get("Event") or record.get("Category") or "").strip()
    if currency is None or not title or not record.get("Date"):
        return None
    try:
        importance = int(record.get("Importance"))
    except (TypeError, ValueError):
        importance = None
    impact = "holiday" if "holiday" in title.lower() else IMPACT.get(importance, "")
    try:
        return Event(source=TradingEconomicsSource.source_id, title=title,
                     country=currency, time=record["Date"], impact=impact,
                     previous=record.get("Previous"), forecast=record.get("Forecast"),
                     actual=record.get("Actual"))
    except ValueError:      # unreadable date
        return None


class TradingEconomicsSource(CalendarSource):
    source_id = "trading_economics"

    def __init__(self, api_key=None, retries=None, timeout=None, clock=None):
        self.api_key = (api_key if api_key is not None
                        else os.environ.get("TRADING_ECONOMICS_KEY", "")).strip()
        self.retries = retries or cfg.FETCH_RETRIES
        self.timeout = timeout or cfg.FETCH_TIMEOUT
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _url(self):
        now = self._clock()
        start = (now - timedelta(days=LOOKBACK_DAYS)).date()
        end = (now + timedelta(days=cfg.STORE_AHEAD_DAYS)).date()
        key = urllib.parse.quote(self.api_key, safe=":")
        return f"{BASE_URL}/{start}/{end}?c={key}&f=json"

    def _get_json(self):
        """The decoded response, or None. Never prints the URL (it holds the key)."""
        request = urllib.request.Request(self._url(), headers={"User-Agent": cfg.USER_AGENT})
        for attempt in range(1, self.retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                print(f"{self.source_id}: attempt {attempt} failed: HTTP {e.code}")
                if e.code in (401, 402, 403, 404, 429):
                    print(f"{self.source_id}: key rejected, plan limit or not available; "
                          "not retrying.")
                    return None
            except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
                print(f"{self.source_id}: attempt {attempt} failed: {type(e).__name__}")
            time.sleep(5 * attempt)
        return None

    def fetch_events(self):
        if not self.api_key:
            print(f"{self.source_id}: no TRADING_ECONOMICS_KEY set, skipping.")
            return None
        data = self._get_json()
        if data is None:
            return None
        if not isinstance(data, list):   # e.g. a "no access" message instead of events
            print(f"{self.source_id}: unexpected response: {str(data)[:100]}")
            return None
        events = [e for e in map(to_event, data) if e is not None]
        print(f"{self.source_id}: {len(events)} events "
              f"({len(data) - len(events)} skipped: other economies or no time)")
        return events
