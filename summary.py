"""Daily summary: one message listing today's events, sent once per day."""
import html
import time

import config as cfg
from state import save_state
from utils import clean, parse_event_time

MAX_CHARS = 3800  # Telegram's limit is 4096; leave headroom for markup


RANK = {**cfg.IMPACT_RANK, "na": -1}   # "na" = no / unknown impact label
NA_STYLE = ("⚫", "N/A")


def _style(level):
    return NA_STYLE if level == "na" else cfg.IMPACT_STYLE[level]


def _level(event):
    level = clean(event.get("impact")).lower()
    return level if level in cfg.IMPACT_RANK else "na"


def todays_events(calendar, now):
    """(level, time, event) for today's events at/above SUMMARY_MIN_IMPACT
    ("all" = every event), most important first, then by time."""
    tz = cfg.DISPLAY_TZ
    today = now.astimezone(tz).date()
    min_rank = -1 if cfg.SUMMARY_MIN_IMPACT == "all" else cfg.IMPACT_RANK[cfg.SUMMARY_MIN_IMPACT]
    found = []
    for event in calendar:
        if not isinstance(event, dict):
            continue
        level = _level(event)
        if RANK[level] < min_rank:
            continue
        when = parse_event_time(event.get("date"))
        if when is None or when.astimezone(tz).date() != today:
            continue
        found.append((level, when, event))
    found.sort(key=lambda i: (-RANK[i[0]], i[1]))
    return found


def _line(when, event):
    local = when.astimezone(cfg.DISPLAY_TZ).strftime("%I:%M %p")
    parts = []
    for label, field in (("Prev", "previous"), ("Fcst", "forecast"),
                         ("Actual", "actual")):
        value = clean(event.get(field))
        if value != "Not available":
            parts.append(f"{label} {html.escape(value)}")
    extra = f" ({' · '.join(parts)})" if parts else ""
    country = html.escape(clean(event.get("country")))
    title = html.escape(clean(event.get("title")))
    return f"🕒 {local}  {country}  <b>{title}</b>{extra}"


def build_messages(events, now):
    """Summary text split into Telegram-sized messages."""
    day = now.astimezone(cfg.DISPLAY_TZ).strftime("%a %d %b %Y")
    header = f"📅 <b>Daily summary - {day} ({cfg.DISPLAY_TZ_LABEL})</b>"
    if not events:
        if cfg.SUMMARY_MIN_IMPACT == "all":
            return [f"{header}\n\nNo events today."]
        label = cfg.IMPACT_STYLE[cfg.SUMMARY_MIN_IMPACT][1].title()
        return [f"{header}\n\nNo events at {label} impact or higher today."]

    lines = [header, f"{len(events)} event(s), most important first"]
    current = None
    for level, when, event in events:
        if level != current:
            current = level
            emoji, label = _style(level)
            count = sum(1 for e in events if e[0] == level)
            lines += ["", f"{emoji} <b>{label.title()} ({count})</b>"]
        lines.append(_line(when, event))

    messages, chunk = [], ""
    for line in lines:
        if chunk and len(chunk) + len(line) + 1 > MAX_CHARS:
            messages.append(chunk)
            chunk = ""
        chunk += ("\n" if chunk else "") + line
    messages.append(chunk)
    return messages


def summary_key(now):
    # Shaped like an event key ("a|b|ISO-time") so state pruning removes it
    # automatically after KEEP_DAYS.
    day = now.astimezone(cfg.DISPLAY_TZ).date().isoformat()
    return f"summary|daily|{day}T00:00:00+00:00"


def maybe_send_summary(calendar, now, notifier, state):
    """Send today's summary once, from SUMMARY_HOUR onwards."""
    if not cfg.SUMMARY_ENABLED:
        return
    if now.astimezone(cfg.DISPLAY_TZ).hour < cfg.SUMMARY_HOUR:
        return
    key = summary_key(now)
    if state.get(key, {}).get("sent"):
        return
    try:
        for message in build_messages(todays_events(calendar, now), now):
            notifier.send(message)
            time.sleep(cfg.SEND_DELAY_SECONDS)
    except Exception as e:  # not marked as sent, so the next run retries
        print("SUMMARY FAILED:", e)
        return
    state[key] = {"sent": True}
    save_state(state)
    print("Daily summary sent.")
