

import os
import json
import html
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

API_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
STATE_FILE = "sent_alerts.json"
IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state):
    temp = STATE_FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(temp, STATE_FILE)


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
            # Feed timestamps should include a timezone.
            raise ValueError("Timestamp has no timezone")
        return dt.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def fetch_calendar():
    request = urllib.request.Request(
        API_URL,
        headers={"User-Agent": "EconomicCalendarAlerts/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))

    if not isinstance(data, list):
        raise RuntimeError("Forex Factory returned an unexpected JSON format.")

    return data


def send_telegram(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Telegram secrets are missing.")

    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "link_preview_options": json.dumps({"is_disabled": True}),
    }).encode()

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")

    message_id = result.get("result", {}).get("message_id")
    print("TELEGRAM_SENT message_id =", message_id)


def make_key(event, event_time):
    country = clean(event.get("country"))
    title = clean(event.get("title"))
    return f"{country}|{title}|{event_time.isoformat()}"


def migrate_old_state(state, events):
    """
    Match old records by normalized title and timestamp.
    This avoids treating a known event as new just because
    an older bot version used a different key format.
    """
    old_records = []
    for key, value in state.items():
        if isinstance(value, dict) and any(
            field in value for field in ("detected", "1h", "30m")
        ):
            old_records.append((key, value))

    for key, event, event_time in events:
        if key in state:
            continue

        title = clean(event.get("title")).lower()
        timestamp = event_time.isoformat()

        for old_key, old_value in old_records:
            old_title = old_key.split("|")[0].lower()
            if title in old_key.lower() and timestamp in old_key:
                state[key] = dict(old_value)
                print("STATE_MIGRATED:", old_key, "->", key)
                break


def main():
    now = datetime.now(UTC)
    end = now + timedelta(days=30)
    state = load_state()

    print("Source: Forex Factory weekly JSON")
    print("Now UTC:", now.isoformat())
    print("Window end UTC:", end.isoformat())

    calendar = fetch_calendar()
    print("Events received:", len(calendar))

    if not calendar:
        raise RuntimeError("Forex Factory returned an empty calendar.")

    events = []
    for event in calendar:
        if not isinstance(event, dict):
            continue
        if clean(event.get("impact")).lower() != "high":
            continue

        event_time = parse_event_time(event.get("date"))
        if event_time is None:
            print("SKIP invalid timestamp:", event.get("title"))
            continue
        if not (now < event_time <= end):
            continue

        events.append((make_key(event, event_time), event, event_time))

    print("Upcoming high-impact events in feed:", len(events))
    print("WARNING: the weekly feed does not cover the complete 30 days.")

    migrate_old_state(state, events)

    for key, event, event_time in sorted(events, key=lambda item: item[2]):
        status = state.setdefault(key, {
            "detected": False,
            "1h": False,
            "30m": False,
        })

        minutes_left = (event_time - now).total_seconds() / 60
        time_ist = event_time.astimezone(IST).strftime("%d-%m-%Y %I:%M %p")

        details = (
            f"🌍 Currency: {html.escape(clean(event.get('country')))}\n"
            f"📌 Event: <b>{html.escape(clean(event.get('title')))}</b>\n"
            f"🕒 Time (IST): {time_ist}\n"
            f"📊 Previous: {html.escape(clean(event.get('previous')))}\n"
            f"🔮 Forecast: {html.escape(clean(event.get('forecast')))}\n"
            f"📈 Actual: {html.escape(clean(event.get('actual')))}\n"
            "Source: Forex Factory"
        )

        print(
            "EVENT:", clean(event.get("title")),
            "| minutes left:", round(minutes_left),
            "| state:", status,
        )

        # Detection alert. Save only after Telegram confirms delivery.
        if not status.get("detected", False):
            print("ACTION: send detection alert")
            send_telegram("🔴 <b>HIGH-IMPACT EVENT DETECTED</b>\n\n" + details)
            status["detected"] = True
            save_state(state)
        else:
            print("SKIP detection: already marked sent")

        # Reminder windows are checked independently.
        if 55 <= minutes_left <= 65 and not status.get("1h", False):
            print("ACTION: send 1-hour reminder")
            send_telegram("⏰ <b>1-HOUR REMINDER</b>\n\n" + details)
            status["1h"] = True
            save_state(state)
        elif not status.get("1h", False):
            print("SKIP 1-hour reminder: outside reminder window")

        if 25 <= minutes_left <= 35 and not status.get("30m", False):
            print("ACTION: send 30-minute reminder")
            send_telegram("⏰ <b>30-MINUTE REMINDER</b>\n\n" + details)
            status["30m"] = True
            save_state(state)
        elif not status.get("30m", False):
            print("SKIP 30-minute reminder: outside reminder window")

    save_state(state)
    print("Calendar check completed.")


if __name__ == "__main__":
    main()
