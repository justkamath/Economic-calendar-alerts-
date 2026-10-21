import json
import html
import os
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta

import config as cfg


# ---------- State ----------
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
    cutoff = now - timedelta(days=cfg.KEEP_DAYS)
    for key in list(state):
        try:
            when = datetime.fromisoformat(key.rsplit("|", 1)[1])
            if when < cutoff:
                del state[key]
        except (IndexError, ValueError):
            pass


# ---------- Helpers ----------
def clean(value):
    if value is None:
        return "Not available"
    value = str(value).strip()
    if value.lower() in ("", "-", "—", "n/a", "na", "null", "none"):
        return "Not available"
    return value


def parse_event_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=cfg.UTC)
        return dt.astimezone(cfg.UTC)
    except (ValueError, TypeError):
        return None


# ---------- Data source ----------
def fetch_calendar():
    request = urllib.request.Request(
        cfg.API_URL, headers={"User-Agent": cfg.USER_AGENT}
    )
    for attempt in range(1, cfg.FETCH_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=cfg.FETCH_TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8"))
            if not isinstance(data, list):
                raise RuntimeError("Unexpected calendar JSON response.")
            return data
        except urllib.error.HTTPError as e:
            print(f"Fetch attempt {attempt} failed: HTTP {e.code}")
            if e.code == 429:
                return None  # rate limited; retry next run
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            print(f"Fetch attempt {attempt} failed: {e}")
        time.sleep(5 * attempt)
    return None


# ---------- Telegram ----------
def send_telegram(message):
    if not cfg.TELEGRAM_BOT_TOKEN or not cfg.TELEGRAM_CHAT_ID:
        raise RuntimeError("Telegram secrets are missing.")

    body = urllib.parse.urlencode({
        "chat_id": cfg.TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "link_preview_options": json.dumps({"is_disabled": True}),
    }).encode()

    request = urllib.request.Request(
        f"{cfg.TELEGRAM_API_BASE}/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage",
        data=body,
    )
    with urllib.request.urlopen(request, timeout=cfg.TELEGRAM_TIMEOUT) as r:
        result = json.loads(r.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")
    print("TELEGRAM_SENT:", result.get("result", {}).get("message_id"))


# ---------- Alerts ----------
def event_key(event, event_time):
    return (
        f"{clean(event.get('country'))}|"
        f"{clean(event.get('title'))}|"
        f"{event_time.isoformat()}"
    )


def event_message(event, event_time, heading):
    local_time = event_time.astimezone(cfg.DISPLAY_TZ).strftime(cfg.TIME_FORMAT)
    return (
        f"{heading}\n\n"
        f"🌍 Country: {html.escape(clean(event.get('country')))}\n"
        f"📌 Event: <b>{html.escape(clean(event.get('title')))}</b>\n"
        f"🕒 Time ({cfg.DISPLAY_TZ_LABEL}): {local_time}\n"
        f"📊 Previous: {html.escape(clean(event.get('previous')))}\n"
        f"🔮 Forecast: {html.escape(clean(event.get('forecast')))}\n"
        f"📈 Actual: {html.escape(clean(event.get('actual')))}\n"
        f"Source: {cfg.SOURCE_NAME}"
    )


def send_once(state, key, status_name, message, force=False):
    status = state.setdefault(key, {"detected": False, "1h": False, "30m": False})
    if status.get(status_name, False) and not force:
        return
    try:
        send_telegram(message)
    except Exception as e:  # one failure must not block other events
        print("SEND FAILED:", status_name, key, e)
        return
    status[status_name] = True
    save_state(state)  # save only after confirmed delivery
    time.sleep(cfg.SEND_DELAY_SECONDS)


def main():
    now = datetime.now(cfg.UTC)
    end = now + timedelta(days=cfg.HORIZON_DAYS)
    state = load_state()

    print("Current UTC:", now.isoformat())
    print("Resend detection mode:", cfg.RESEND_DETECTION)

    calendar = fetch_calendar()
    if calendar is None:
        print("Could not fetch calendar this run. Exiting cleanly.")
        return
    print("Events received:", len(calendar))

    upcoming = []
    for event in calendar:
        if not isinstance(event, dict):
            continue
        if clean(event.get("impact")).lower() != cfg.ALERT_IMPACT:
            continue
        event_time = parse_event_time(event.get("date"))
        if event_time is None or not (now < event_time <= end):
            continue
        upcoming.append((event_key(event, event_time), event, event_time))

    print("Upcoming high-impact events:", len(upcoming))

    h_lo, h_hi = cfg.ONE_HOUR_WINDOW
    m_lo, m_hi = cfg.THIRTY_MIN_WINDOW

    for key, event, event_time in sorted(upcoming, key=lambda i: i[2]):
        minutes_left = (event_time - now).total_seconds() / 60

        send_once(
            state, key, "detected",
            event_message(event, event_time, "🔴 <b>HIGH-IMPACT EVENT DETECTED</b>"),
            force=cfg.RESEND_DETECTION,
        )

        if h_lo < minutes_left <= h_hi:
            send_once(state, key, "1h",
                      event_message(event, event_time, "⏰ <b>1-HOUR REMINDER</b>"))
        elif m_lo < minutes_left <= m_hi:
            send_once(state, key, "30m",
                      event_message(event, event_time, "⏰ <b>30-MINUTE REMINDER</b>"))

    prune_state(state, now)
    save_state(state)
    print("Calendar check completed.")


if __name__ == "__main__":
    main()
