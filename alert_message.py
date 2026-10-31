"""Layout of a single event alert (detected / 1-hour / 30-minute messages).

An "event card" in three calm blocks:
  1. level colour + name, and where we are (new / 1 hour / 30 minutes)
  2. the event: name, flag + currency, time, day, countdown (bold, easy to scan)
  3. the numbers (prior / forecast / actual) aligned in a monospace <pre> block
Same level colours and names as the daily summary."""
import html
import re
from datetime import datetime

import config as cfg
from summary import FLAGS
from utils import clean

TITLES = {"high": "HIGH IMPACT", "medium": "MEDIUM IMPACT", "low": "LOW IMPACT",
          "holiday": "BANK HOLIDAY"}
STAGES = {"detected": ("🔔", "New on your calendar"),
          "1h": ("⏰", "Starts in about 1 hour"),
          "30m": ("⏳", "Starts in about 30 minutes")}
CURRENCY_NAMES = {"USD": "US Dollar", "EUR": "Euro", "GBP": "British Pound",
                  "JPY": "Japanese Yen", "AUD": "Australian Dollar",
                  "NZD": "New Zealand Dollar", "CAD": "Canadian Dollar",
                  "CHF": "Swiss Franc", "CNY": "Chinese Yuan"}
CALM_NOTE = "🌿 Just a heads-up, nothing to do right now."
NO_VALUE = "n/a"
PENDING = "not out yet"
NO_FIGURES = "📊 <i>No figures published for this one.</i>"
MAX_VALUE = 14   # keeps the numbers block narrow enough for a phone screen
_SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}


def heading(stage, level):
    """Two lines: the level, then where we are, e.g.
    '🟠 <b>MEDIUM IMPACT</b>' / '⏰ <i>Starts in about 1 hour</i>'"""
    emoji = cfg.IMPACT_STYLE[level][0]
    icon, text = STAGES[stage]
    return f"{emoji} <b>{TITLES[level]}</b>\n{icon} <i>{text}</i>"


def _number(text):
    """'0.6%' -> 0.6, '29.0K' -> 29000.0, '-1.2B' -> -1.2e9; None if not numeric."""
    match = re.fullmatch(r"([-+]?\d+(?:[.,]\d+)*)\s*([kmbtKMBT])?\s*%?", text.strip())
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    return value * _SUFFIX.get((match.group(2) or "").lower(), 1)


def _known(event, field):
    value = clean(event.get(field))
    return None if value == "Not available" else value[:MAX_VALUE]


def _trend(previous, forecast):
    """How the forecast compares with the previous figure."""
    a, b = _number(previous or ""), _number(forecast or "")
    if a is None or b is None:
        return ""
    if b > a:
        return "  ▲ up"
    return "  ▼ down" if b < a else "  = flat"


def _verdict(actual, forecast):
    a, b = _number(actual or ""), _number(forecast or "")
    if a is None or b is None:
        return ""
    if a > b:
        return "  ▲ above"
    return "  ▼ below" if a < b else "  = on forecast"


def _day_label(local, now_local):
    """'Today, Fri 30 Oct' / 'Tomorrow, Sat 31 Oct' / 'Fri 06 Nov'."""
    delta = (local.date() - now_local.date()).days
    date = local.strftime("%a %d %b")
    return {0: f"Today, {date}", 1: f"Tomorrow, {date}"}.get(delta, date)


def _countdown(event_time, now):
    """'now', '45 min', '1 hour', '1 hr 15 min', '3 days 4 hrs'."""
    minutes = int((event_time - now).total_seconds() // 60)
    if minutes <= 0:
        return "now"
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days} day{'s' if days != 1 else ''} {hours} hr{'s' if hours != 1 else ''}"
    if hours:
        if not mins:
            return f"{hours} hour{'s' if hours != 1 else ''}"
        return f"{hours} hr {mins} min"
    return f"{mins} min"


def _table(rows):
    """Plain aligned rows (label column, then value)."""
    left = max(len(label) for label, _ in rows)
    return "\n".join(f"{label.ljust(left)}  {value}" for label, value in rows)


def _currency(country):
    name = CURRENCY_NAMES.get(country.upper())
    return f"{country} ({name})" if name else country


def _numbers_lines(event):
    previous, forecast = _known(event, "previous"), _known(event, "forecast")
    actual = _known(event, "actual")
    if not (previous or forecast or actual):
        return [NO_FIGURES]
    table = _table([
        ("Prior", previous or NO_VALUE),
        ("Forecast", (forecast or NO_VALUE) + _trend(previous, forecast)),
        ("Actual", (actual + _verdict(actual, forecast)) if actual else PENDING),
    ])
    return [f"<pre>{html.escape(table)}</pre>"]


def event_message(event, event_time, heading_text, now=None):
    now = now or datetime.now(cfg.UTC)
    local = event_time.astimezone(cfg.DISPLAY_TZ)
    now_local = now.astimezone(cfg.DISPLAY_TZ)
    country = clean(event.get("country"))
    left = _countdown(event_time, now)
    return "\n".join([
        heading_text,
        "",
        f"<b>{html.escape(clean(event.get('title')))}</b>",
        f"{FLAGS.get(country.upper(), '🌐')} {html.escape(_currency(country))}",
        "",
        f"🕒 <b>{local.strftime('%I:%M %p').lstrip('0')} {cfg.DISPLAY_TZ_LABEL}</b>",
        f"📅 {_day_label(local, now_local)}",
        "⏳ Starting now" if left == "now" else f"⏳ In {left}",
        "",
        *_numbers_lines(event),
        f"<i>{CALM_NOTE}</i>",
        f"<i>Source: {html.escape(cfg.SOURCE_NAME)}</i>",
    ])
