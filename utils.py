"""Small shared helpers (no I/O, no config coupling beyond UTC)."""
from datetime import datetime

import config as cfg


def clean(value):
    """Normalise empty / placeholder values to 'Not available'."""
    if value is None:
        return "Not available"
    value = str(value).strip()
    if value.lower() in ("", "-", "—", "n/a", "na", "null", "none"):
        return "Not available"
    return value


def parse_event_time(value):
    """Parse an ISO timestamp into an aware UTC datetime, or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=cfg.UTC)
        return dt.astimezone(cfg.UTC)
    except (ValueError, TypeError):
        return None
