"""Alert logic: filter upcoming events, build messages, send each stage once."""
import html
import time
from datetime import timedelta

import config as cfg
from state import prune_state, save_state
from utils import clean, parse_event_time


def event_key(event, event_time):
    return (
        f"{clean(event.get('country'))}|"
        f"{clean(event.get('title'))}|"
        f"{event_time.isoformat()}"
    )


def impact_of(event):
    """Normalised impact level ('high', 'medium', 'low', 'holiday') or None."""
    level = clean(event.get("impact")).lower()
    return level if level in cfg.IMPACT_RANK else None


def event_message(event, event_time, heading):
    local_time = event_time.astimezone(cfg.DISPLAY_TZ).strftime(cfg.TIME_FORMAT)
    return (
        f"{heading}\n\n"
        f"🌍 Country: {html.escape(clean(event.get('country')))}\n"
        f"📌 Event: <b>{html.escape(clean(event.get('title')))}</b>\n"
        f"⚡ Impact: {html.escape(clean(event.get('impact')))}\n"
        f"🕒 Time ({cfg.DISPLAY_TZ_LABEL}): {local_time}\n"
        f"📊 Previous: {html.escape(clean(event.get('previous')))}\n"
        f"🔮 Forecast: {html.escape(clean(event.get('forecast')))}\n"
        f"📈 Actual: {html.escape(clean(event.get('actual')))}\n"
        f"Source: {cfg.SOURCE_NAME}"
    )


def select_upcoming(calendar, now):
    """Events at or above ALERT_MIN_IMPACT in the next HORIZON_DAYS,
    most important first, then by time."""
    min_rank = cfg.IMPACT_RANK[cfg.ALERT_MIN_IMPACT]
    end = now + timedelta(days=cfg.HORIZON_DAYS)
    upcoming = []
    for event in calendar:
        if not isinstance(event, dict):
            continue
        level = impact_of(event)
        if level is None or cfg.IMPACT_RANK[level] < min_rank:
            continue
        event_time = parse_event_time(event.get("date"))
        if event_time is None or not (now < event_time <= end):
            continue
        upcoming.append((event_key(event, event_time), event, event_time))
    return sorted(
        upcoming, key=lambda i: (-cfg.IMPACT_RANK[impact_of(i[1])], i[2])
    )


def send_once(notifier, state, key, status_name, message, force=False):
    status = state.setdefault(key, {"detected": False, "1h": False, "30m": False})
    if status.get(status_name, False) and not force:
        return
    try:
        notifier.send(message)
    except Exception as e:  # one failure must not block other events
        print("SEND FAILED:", status_name, key, e)
        return
    status[status_name] = True
    save_state(state)  # save only after confirmed delivery
    time.sleep(cfg.SEND_DELAY_SECONDS)


def process(upcoming, now, notifier, state):
    h_lo, h_hi = cfg.ONE_HOUR_WINDOW
    m_lo, m_hi = cfg.THIRTY_MIN_WINDOW

    for key, event, event_time in upcoming:
        minutes_left = (event_time - now).total_seconds() / 60

        emoji, label = cfg.IMPACT_STYLE[impact_of(event)]
        send_once(
            notifier, state, key, "detected",
            event_message(event, event_time,
                          f"{emoji} <b>{label}-IMPACT EVENT DETECTED</b>"),
            force=cfg.RESEND_DETECTION,
        )

        if h_lo < minutes_left <= h_hi:
            send_once(notifier, state, key, "1h",
                      event_message(event, event_time, "⏰ <b>1-HOUR REMINDER</b>"))
        elif m_lo < minutes_left <= m_hi:
            send_once(notifier, state, key, "30m",
                      event_message(event, event_time, "⏰ <b>30-MINUTE REMINDER</b>"))

    prune_state(state, now)
    save_state(state)
