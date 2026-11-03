"""Alert logic: filter upcoming events, build messages, send each stage once."""
import html
import time
from datetime import timedelta

import config as cfg
from alert_message import event_message, heading  # noqa: F401  (re-exported)
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


def _state_key(event, event_time, state):
    """The key an event is tracked under. A merged event can carry the keys of
    every source's version ("ids"); if one of them already has alert history,
    keep using it, so a slower source never causes a repeat alert."""
    ids = event.get("ids")
    if state and isinstance(ids, list):
        for candidate in ids:
            if candidate in state:
                return candidate
    return event_key(event, event_time)


def select_upcoming(calendar, now, min_rank=None, state=None):
    """Events at or above ALERT_MIN_IMPACT in the next HORIZON_DAYS,
    most important first, then by time."""
    if min_rank is None:
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
        upcoming.append((_state_key(event, event_time, state), event, event_time))
    return sorted(
        upcoming, key=lambda i: (-cfg.IMPACT_RANK[impact_of(i[1])], i[2])
    )


def send_once(notifier, state, key, status_name, message, force=False,
              recipients=None, on_blocked=None):
    """Send one alert stage; mark it done only after Telegram confirms it.

    With `recipients` (chat IDs) the message goes to each of them separately; one
    person failing never stops the others, and the stage counts as done once at
    least one person got it. Without it, the notifier's default chats are used."""
    status = state.setdefault(key, {"detected": False, "1h": False, "30m": False})
    if status.get(status_name, False) and not force:
        return
    if recipients is None:
        try:
            notifier.send(message)
        except Exception as e:  # not marked as sent, so the next run retries
            print("SEND FAILED:", status_name, key, e)
            return
    else:
        delivered = 0
        for number, chat_id in enumerate(recipients, start=1):
            try:
                notifier.send(message, chat_id=chat_id)
                delivered += 1
            except Exception as e:
                print(f"SEND FAILED: {status_name} {key} to chat #{number}: {e}")
                if getattr(e, "code", None) == 403 and on_blocked:
                    on_blocked(chat_id)   # they blocked the bot: stop trying
            time.sleep(cfg.SEND_DELAY_SECONDS)
        if not delivered:
            return
    time.sleep(cfg.SEND_DELAY_SECONDS)
    status[status_name] = True
    save_state(state)  # save only after confirmed delivery


def process(upcoming, now, notifier, state, audience=None):
    """Send due alerts. With an `audience`, each event goes only to the people
    whose level and currencies match it; without one, to the notifier's chats."""
    h_lo, h_hi = cfg.ONE_HOUR_WINDOW
    m_lo, m_hi = cfg.THIRTY_MIN_WINDOW

    for key, event, event_time in upcoming:
        level = impact_of(event)
        minutes_left = (event_time - now).total_seconds() / 60
        extra = {}
        if audience is not None:
            recipients = audience.recipients_for(cfg.IMPACT_RANK[level],
                                                 clean(event.get("country")))
            if not recipients:
                continue    # nobody wants this one (yet): leave it unmarked
            extra = {"recipients": recipients, "on_blocked": audience.remove}

        send_once(notifier, state, key, "detected",
                  event_message(event, event_time, heading("detected", level)),
                  force=cfg.RESEND_DETECTION, **extra)

        if h_lo < minutes_left <= h_hi:
            send_once(notifier, state, key, "1h",
                      event_message(event, event_time, heading("1h", level)), **extra)
        elif m_lo < minutes_left <= m_hi:
            send_once(notifier, state, key, "30m",
                      event_message(event, event_time, heading("30m", level)), **extra)

    prune_state(state, now)
    save_state(state)
