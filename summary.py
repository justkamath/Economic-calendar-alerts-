"""Daily summary: one friendly message listing today's events, once per day."""
import html
import time
from datetime import timedelta

import config as cfg
from state import save_state
from utils import clean, parse_event_time

MAX_CHARS = 3800  # Telegram's limit is 4096; leave headroom for markup

RANK = {**cfg.IMPACT_RANK, "na": -1}   # "na" = no / unknown impact label
ORDER = ("high", "medium", "low", "holiday", "na")
NA_STYLE = ("⚫", "N/A")

TITLES = {
    "high": "HIGH IMPACT",
    "medium": "MEDIUM IMPACT",
    "low": "LOW IMPACT",
    "holiday": "BANK HOLIDAYS",
    "na": "UNRATED",
}
SHORT = {"high": "High", "medium": "Medium", "low": "Low",
         "holiday": "Holiday", "na": "Unrated"}
FLAGS = {"USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵", "AUD": "🇦🇺",
         "NZD": "🇳🇿", "CAD": "🇨🇦", "CHF": "🇨🇭", "CNY": "🇨🇳"}
RULE = "──────────────"


def _style(level):
    return NA_STYLE if level == "na" else cfg.IMPACT_STYLE[level]


def _level(event):
    level = clean(event.get("impact")).lower()
    return level if level in cfg.IMPACT_RANK else "na"


def events_for_day(calendar, day):
    """(level, time, event) for events on local date `day` at/above
    SUMMARY_MIN_IMPACT ("all" = everything), most important first, then time."""
    tz = cfg.DISPLAY_TZ
    min_rank = -1 if cfg.SUMMARY_MIN_IMPACT == "all" else cfg.IMPACT_RANK[cfg.SUMMARY_MIN_IMPACT]
    found = []
    for event in calendar:
        if not isinstance(event, dict):
            continue
        level = _level(event)
        if RANK[level] < min_rank:
            continue
        when = parse_event_time(event.get("date"))
        if when is None or when.astimezone(tz).date() != day:
            continue
        found.append((level, when, event))
    found.sort(key=lambda i: (-RANK[i[0]], i[1]))
    return found


def todays_events(calendar, now):
    return events_for_day(calendar, now.astimezone(cfg.DISPLAY_TZ).date())


# ---------- formatting ----------
def _time(when):
    return when.astimezone(cfg.DISPLAY_TZ).strftime("%I:%M %p").lstrip("0")


def _who(event):
    country = clean(event.get("country"))
    flag = FLAGS.get(country.upper(), "🌐")
    return f"{flag} {html.escape(country)}"


def _details(event):
    parts = []
    for label, field in (("Previous", "previous"), ("Forecast", "forecast"),
                         ("Actual", "actual")):
        value = clean(event.get(field))
        if value != "Not available":
            parts.append(f"{label} <b>{html.escape(value)}</b>")
    return "↳ " + "  |  ".join(parts) if parts else ""


def _bullet(level, event):
    """Same layout for every impact level (no space-based alignment, since
    Telegram uses a proportional font)."""
    title = html.escape(clean(event.get("title")))
    if level == "high":
        title = f"<b>{title}</b>"
    lines = [f"➤ {_who(event)} — {title}"]
    details = _details(event)
    if details:
        lines.append(details)
    return lines


def _section(level, items):
    """Events of one level, grouped under a heading per start time."""
    lines, current = [], None
    for when, event in items:
        stamp = _time(when)
        if stamp != current:
            current = stamp
            lines += ["", f"🕒 <b>{stamp}</b>"]
        lines += _bullet(level, event) + [""]  # blank line after each event
    return lines


def _counts(events):
    counts = {}
    for level, _, _ in events:
        counts[level] = counts.get(level, 0) + 1
    return counts


def _glance(events):
    counts = _counts(events)
    return "  |  ".join(
        f"{_style(level)[0]} {counts[level]} {SHORT[level]}"
        for level in ORDER if level in counts
    )


def _events(n):
    return f"{n} event" + ("" if n == 1 else "s")


def _greeting(now):
    hour = now.astimezone(cfg.DISPLAY_TZ).hour
    if hour < 12:
        return "Good morning"
    return "Good afternoon" if hour < 17 else "Good evening"


def build_messages(events, now, tomorrow=None):
    """Summary text split into Telegram-sized messages.
    `tomorrow` (optional) is tomorrow's event list, for the preview line."""
    local = now.astimezone(cfg.DISPLAY_TZ)
    lines = [f"📅 <b>{local.strftime('%A, %d %b %Y')}</b>"]

    if not events:
        lines.append(f"{_greeting(now)}! 😌 It's a quiet day, "
                     "nothing on the calendar today.")
    else:
        lines += [
            f"{_greeting(now)}! Here's what's on today.",
            f"🕐 All times in {cfg.DISPLAY_TZ_LABEL}",
            "",
            f"<b>Today at a glance</b>  ({_events(len(events))})",
            _glance(events),
        ]
        for level in ORDER:
            items = [(w, e) for lv, w, e in events if lv == level]
            if not items:
                continue
            emoji, _ = _style(level)
            lines += ["", RULE, f"{emoji} <b>{TITLES[level]}</b>"]
            lines += _section(level, items)

    if tomorrow is not None:
        lines += ["", RULE]
        if tomorrow:
            lines.append(f"👀 <b>Tomorrow:</b> {_events(len(tomorrow))}  "
                         f"({_glance(tomorrow)})")
        else:
            lines.append("👀 <b>Tomorrow:</b> nothing scheduled yet")

    tidy = []  # never more than one blank line in a row
    for line in lines:
        if line == "" and tidy and tidy[-1] == "":
            continue
        tidy.append(line)

    messages, chunk = [], ""
    for line in tidy:
        if chunk and len(chunk) + len(line) + 1 > MAX_CHARS:
            messages.append(chunk.strip("\n"))
            chunk = ""
        chunk += ("\n" if chunk else "") + line
    messages.append(chunk.strip("\n"))
    return messages


# ---------- sending ----------
def summary_key(now):
    # Shaped like an event key ("a|b|ISO-time") so state pruning removes it
    # automatically after KEEP_DAYS.
    day = now.astimezone(cfg.DISPLAY_TZ).date().isoformat()
    return f"summary|daily|{day}T00:00:00+00:00"


def maybe_send_summary(calendar, now, notifier, state):
    """Send today's summary once, from SUMMARY_HOUR onwards.
    With RESEND_SUMMARY it is sent now regardless of time or earlier sends."""
    if not cfg.SUMMARY_ENABLED:
        if cfg.RESEND_SUMMARY:
            print("Summary resend requested, but SUMMARY_ENABLED is false.")
        return
    key = summary_key(now)
    if not cfg.RESEND_SUMMARY:
        if now.astimezone(cfg.DISPLAY_TZ).hour < cfg.SUMMARY_HOUR:
            return
        if state.get(key, {}).get("sent"):
            return
    try:
        today = now.astimezone(cfg.DISPLAY_TZ).date()
        events = events_for_day(calendar, today)
        tomorrow = events_for_day(calendar, today + timedelta(days=1))
        for message in build_messages(events, now, tomorrow):
            notifier.send(message)
            time.sleep(cfg.SEND_DELAY_SECONDS)
    except Exception as e:  # not marked as sent, so the next run retries
        print("SUMMARY FAILED:", e)
        return
    state[key] = {"sent": True}
    save_state(state)
    print("Daily summary sent.")
