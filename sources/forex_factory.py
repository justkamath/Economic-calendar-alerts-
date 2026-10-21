"""Forex Factory calendar feed (faireconomy JSON mirror)."""
import json
import time
import urllib.error
import urllib.request

import config as cfg


class ForexFactorySource:
    name = cfg.SOURCE_NAME

    def __init__(self, url=None, user_agent=None, retries=None, timeout=None):
        self.url = url or cfg.API_URL
        self.user_agent = user_agent or cfg.USER_AGENT
        self.retries = retries or cfg.FETCH_RETRIES
        self.timeout = timeout or cfg.FETCH_TIMEOUT

    def fetch_calendar(self):
        """Return a list of event dicts, or None if unavailable this run."""
        request = urllib.request.Request(
            self.url, headers={"User-Agent": self.user_agent}
        )
        for attempt in range(1, self.retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as r:
                    data = json.loads(r.read().decode("utf-8"))
                if not isinstance(data, list):
                    raise RuntimeError("Unexpected calendar JSON response.")
                return data
            except urllib.error.HTTPError as e:
                print(f"Fetch attempt {attempt} failed: HTTP {e.code}")
                if e.code == 429:
                    return None  # rate limited; retry next run
            except (urllib.error.URLError, json.JSONDecodeError,
                    TimeoutError, RuntimeError) as e:
                print(f"Fetch attempt {attempt} failed: {e}")
            time.sleep(5 * attempt)
        return None
