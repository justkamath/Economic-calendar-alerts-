"""Forex Factory calendar feeds (faireconomy JSON mirror).

Each feed covers one calendar week. Several feeds (this week, next week) are
fetched and merged; one feed failing never blocks the others.
"""
import json
import time
import urllib.error
import urllib.request

import config as cfg


class ForexFactorySource:
    name = cfg.SOURCE_NAME

    def __init__(self, urls=None, user_agent=None, retries=None, timeout=None):
        self.urls = tuple(urls or cfg.API_URLS)
        self.user_agent = user_agent or cfg.USER_AGENT
        self.retries = retries or cfg.FETCH_RETRIES
        self.timeout = timeout or cfg.FETCH_TIMEOUT

    def _fetch_one(self, url):
        """Return a list of events from one feed, or None if unavailable."""
        request = urllib.request.Request(
            url, headers={"User-Agent": self.user_agent}
        )
        for attempt in range(1, self.retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as r:
                    data = json.loads(r.read().decode("utf-8"))
                if not isinstance(data, list):
                    raise RuntimeError("Unexpected calendar JSON response.")
                return data
            except urllib.error.HTTPError as e:
                print(f"Fetch attempt {attempt} failed ({url}): HTTP {e.code}")
                if e.code in (404, 429):
                    return None  # not published yet / rate limited: next run
            except (urllib.error.URLError, json.JSONDecodeError,
                    TimeoutError, RuntimeError) as e:
                print(f"Fetch attempt {attempt} failed ({url}): {e}")
            time.sleep(5 * attempt)
        return None

    def fetch_calendar(self):
        """Merged, de-duplicated events from all feeds; None if every feed failed."""
        merged, seen, got_any = [], set(), False
        for url in self.urls:
            data = self._fetch_one(url)
            if data is None:
                continue
            got_any = True
            print(f"Feed OK ({url}): {len(data)} events")
            for event in data:
                if not isinstance(event, dict):
                    continue
                ident = (event.get("country"), event.get("title"), event.get("date"))
                if ident in seen:
                    continue
                seen.add(ident)
                merged.append(event)
        return merged if got_any else None
