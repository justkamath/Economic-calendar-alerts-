
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
            dt = dt.replace(tzinfo=UTC)
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
        raise RuntimeError("Unexpected Forex Factory JSON response.")
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

    print("TELEGRAM_SENT:", result.get("result", {}).get("message_id"))


def event_key(event, event_time):
    # Stable key used consistently by this version.
    return (
        f"{clean(event.get('country'))}|"
        f"{clean(event.get('title'))}|"
        f"{event_time.isoformat()}"
    )


def event_message(event, event_time, heading):
    time_ist = event_time.astimezone(IST).strftime("%d-%m-%Y %I:%M %p")
    return (
        f"{heading}\n\n"
        f"🌍 Country: {html.escape(clean(event.get('country')))}\n"
        f"📌 Event: <b>{html.escape(clean(event.get('title')))}</b>\n"
        f"🕒 Time (IST): {time_ist}\n"
        f"📊 Previous: {html.escape(clean(event.get('previous')))}\n"
        f"🔮 Forecast: {html.escape(clean(event.get('forecast')))}\n"
        f"📈 Actual: {html.escape(clean(event.get('actual')))}\n"
        "Source: Forex Factory"
    )


def send_once(state, key, status_name, message):
    status = state.setdefault(key, {
        "detected": False,
        "1h": False,
        "30m": False,
    })

    if status.get(status_name, False):
        print("SKIP already sent:", status_name, key)
        return

    print("SENDING:", status_name, key)
    send_telegram(message)

    # Save only after Telegram confirms success.
    status[status_name] = True
    save_state(state)
    print("STATE_SAVED:", status_name, key)


def main():
    now = datetime.now(UTC)
    end = now + timedelta(days=30)
    state = load_state()

    print("Source: Forex Factory weekly JSON")
    print("Current UTC:", now.isoformat())
    print("Configured horizon: 30 days (feed coverage is only this week)")

    calendar = fetch_calendar()
    print("Events received:", len(calendar))

    upcoming = []
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

        upcoming.append((event_key(event, event_time), event, event_time))

    print("Upcoming high-impact events:", len(upcoming))

    for key, event, event_time in sorted(
        upcoming, key=lambda item: item[2]
    ):
        minutes_left = (event_time - now).total_seconds() / 60
        print(
            "EVENT:", clean(event.get("title")),
            "| minutes left:", round(minutes_left),
            "| key:", key,
        )

        send_once(
            state,
            key,
            "detected",
            event_message(
                event, event_time,
                "🔴 <b>HIGH-IMPACT EVENT DETECTED</b>"
            ),
        )

        if 55 <= minutes_left <= 65:
            send_once(
                state, key, "1h",
                event_message(event, event_time, "⏰ <b>1-HOUR REMINDER</b>"),
            )

        if 25 <= minutes_left <= 35:
            send_once(
                state, key, "30m",
                event_message(event, event_time, "⏰ <b>30-MINUTE REMINDER</b>"),
            )

    print("Calendar check completed.")


if __name__ == "__main__":
    main()
