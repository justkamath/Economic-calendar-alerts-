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


def prune_state(state, now):
    """Drop entries whose event time is older than KEEP_DAYS."""
    cutoff = now - timedelta(days=cfg.KEEP_DAYS)
    for key in list(state):
        try:
            when = datetime.fromisoformat(key.rsplit("|", 1)[1])
            if when < cutoff:
                del state[key]
        except (IndexError, ValueError):
            pass  # non-event keys (metadata) are left alone
