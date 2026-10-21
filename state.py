"""Persistent record of which alerts were already sent (sent_alerts.json)."""
import json
import os
from datetime import datetime, timedelta

import config as cfg


def load_state():
    try:
        with open(cfg.STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state):
    temp = cfg.STATE_FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(temp, cfg.STATE_FILE)


def _event_time_from_key(key):
    """Return the event datetime for a 'country|title|iso-time' key, else None."""
    parts = key.split("|")
    if len(parts) < 3:
        return None  # legacy/metadata key, not produced by the current code
    try:
        return datetime.fromisoformat(parts[-1])
    except ValueError:
        return None


def prune_state(state, now):
    """Drop entries older than KEEP_DAYS and legacy keys the app no longer uses."""
    cutoff = now - timedelta(days=cfg.KEEP_DAYS)
    for key in list(state):
        when = _event_time_from_key(key)
        if when is None or when < cutoff:
            del state[key]
