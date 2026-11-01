"""Shared JSON fetching for sources that use an API key.

Never prints the URL (it may contain the key), only the source's label."""
import json
import time
import urllib.error
import urllib.request

import config as cfg

NO_RETRY = (401, 402, 403, 404, 429)   # key rejected / plan limit / not there / rate limited


def get_json(url, label, retries=None, timeout=None, headers=None):
    """The decoded JSON response, or None if it could not be fetched."""
    retries = retries or cfg.FETCH_RETRIES
    timeout = timeout or cfg.FETCH_TIMEOUT
    request = urllib.request.Request(
        url, headers={"User-Agent": cfg.USER_AGENT, **(headers or {})})
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            print(f"{label}: attempt {attempt} failed: HTTP {e.code}")
            if e.code in NO_RETRY:
                print(f"{label}: key rejected, plan limit or endpoint not available; "
                      "not retrying.")
                return None
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            print(f"{label}: attempt {attempt} failed: {type(e).__name__}")
        time.sleep(5 * attempt)
    return None
