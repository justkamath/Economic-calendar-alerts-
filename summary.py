"""Daily summary: a day planner listing today's events in the order they happen,
grouped by part of the day, with a colour dot for importance. Sent once a day."""
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
TITLES = {"high": "HIGH IMPACT", "medium": "MEDIUM IMPACT", "low": "LOW IMPACT",
          "holiday": "BANK HOLIDAYS", "na": "UNRATED"}
SHORT = {"high": "High", "medium": "Medium", "low": "Low",
         "holiday": "Holiday", "na": "Unrated"}
FLAGS = {"USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵", "AUD": "🇦🇺",
         "NZD": "🇳🇿", "CAD": "🇨🇦", "CHF": "🇨🇭", "CNY": "🇨🇳"}

# (first hour, last hour + 1, emoji, name) in local time (DISPLAY_TIMEZONE)
PARTS = (
    (0, 5, "🌙", "EARLY HOURS"),
    (5, 12, "🌅", "MORNING"),
    (12, 17, "☀️", "AFTERNOON"),
    (17, 21, "🌆", "EVENING"),
    (21, 24, "🌃", "NIGHT"),
)
NUMBERS_FOR = ("high", "medium")   # Previous / Forecast / Actual shown for these
FOCUS_MAX = 8                      # currencies listed on the "In focus" line


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


def _flag(country):
    return FLAGS.get(country.upper(), "🌐")


def _events(n):
    return f"{n} event" + ("" if n == 1 else "s")


def _glance(events, sep="   "):
    counts = {}
    for level, _, _ in events:
        counts[level] = counts.get(level, 0) + 1
    return sep.join(f"{_style(level)[0]} {counts[level]} {SHORT[level]}"
                    for level in ORDER if level in counts)


def _greeting(now):
    hour = now.astimezone(cfg.DISPLAY_TZ).hour
    if hour < 12:
        return "Good morning"
    return "Good afternoon" if hour < 17 else "Good evening"


def _focus(events):
    """Currencies in play today, the ones with most High events first."""
    stats = {}
    for level, _, event in events:
        cur = clean(event.get("country"))
        high, total = stats.get(cur, (0, 0))
        stats[cur] = (high + (level == "high"), total + 1)
    ranked = sorted(stats, key=lambda c: (-stats[c][0], -stats[c][1], c))
    return "  ".join(f"{_flag(c)} {html.escape(c)}" for c in ranked[:FOCUS_MAX])


def _details(event):
    parts = []
    for label, field in (("Previous", "previous"), ("Forecast", "forecast"),
                         ("Actual", "actual")):
        value = clean(event.get(field))
        if value != "Not available":
            parts.append(f"{label} <b>{html.escape(value)}</b>")
    return "    ↳ " + "  |  ".join(parts) if parts else ""


def _event_lines(level, when, event):
    emoji = _style(level)[0]
    country = clean(event.get("country"))
    lines = [f"{emoji} <b>{_time(when)}</b>  {_flag(country)} {html.escape(country)}",
             f"    <b>{html.escape(clean(event.get('title')))}</b>"]
    if level in NUMBERS_FOR:
        details = _details(event)
        if details:
            lines.append(details)
    lines.append("")  # blank line after every event
    return lines


def _part_of(when):
    hour = when.astimezone(cfg.DISPLAY_TZ).hour
    for start, end, emoji, name in PARTS:
        if start <= hour < end:
            return emoji, name
    return PARTS[-1][2], PARTS[-1][3]


def _tomorrow_lines(tomorrow):
    if tomorrow is None:
        return []
    if tomorrow:
        return [f"👀 <b>Tomorrow:</b> {_events(len(tomorrow))}  "
                f"({_glance(tomorrow, '  |  ')})"]
    return ["👀 <b>Tomorrow:</b> nothing scheduled yet"]


def _chunk(lines):
    """Split into Telegram-sized messages (no double blank lines)."""
    tidy = []
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


def _level_section(level, items, now):
    """One impact level as its own message: title, date, then its events in
    time order grouped by part of the day (same layout for every level)."""
    emoji, _ = _style(level)
    day = now.astimezone(cfg.DISPLAY_TZ).strftime("%A, %d %b")
    lines = [f"{emoji} <b>{TITLES[level]}</b>  ({_events(len(items))})",
             f"{day} ({cfg.DISPLAY_TZ_LABEL})", ""]
    current = None
    for when, event in sorted(items, key=lambda i: i[0]):
        part = _part_of(when)
        if part != current:
            current = part
            lines += [f"{part[0]} <b>{part[1]}</b>"]
        lines += _event_lines(level, when, event)
    return lines


def build_messages(events, now, tomorrow=None):
    """The summary as a list of messages: a short overview first, then one
    message per impact level (High, Medium, Low, ...), most important first.
    `tomorrow` (optional) is tomorrow's event list, shown on the overview."""
    local = now.astimezone(cfg.DISPLAY_TZ)
    overview = [f"📅 <b>{local.strftime('%A, %d %b %Y')}</b>"]
    sections = [overview]

    if not events:
        overview.append(f"{_greeting(now)}! 😌 It's a quiet day, "
                        "nothing on the calendar today.")
    else:
        overview += [f"{_greeting(now)}! Here's your day ({cfg.DISPLAY_TZ_LABEL})",
                     "", _glance(events), f"🌍 In focus: {_focus(events)}"]
    if tomorrow is not None:
        overview += [""] + _tomorrow_lines(tomorrow)
    if events:
        overview += ["", "👇 Details below, one message per impact level"]
        for level in ORDER:
            items = [(w, e) for lv, w, e in events if lv == level]
            if items:
                sections.append(_level_section(level, items, now))

    messages = []
    for section in sections:
        messages += _chunk(section)
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
    # Progress is saved after every message, so if a send fails part-way the
    # next run carries on from the next message instead of repeating earlier ones.
    done = 0 if cfg.RESEND_SUMMARY else state.get(key, {}).get("parts_sent", 0)
    try:
        today = now.astimezone(cfg.DISPLAY_TZ).date()
        events = events_for_day(calendar, today)
        tomorrow = events_for_day(calendar, today + timedelta(days=1))
        for number, message in enumerate(build_messages(events, now, tomorrow)):
            if number < done:
                continue
            notifier.send(message)
            state[key] = {"sent": False, "parts_sent": number + 1}
            save_state(state)
            time.sleep(cfg.SEND_DELAY_SECONDS)
    except Exception as e:  # not marked as sent, so the next run retries
        print("SUMMARY FAILED:", e)
        return
    state[key] = {"sent": True}
    save_state(state)
    print("Daily summary sent.")
