
import urllib.request
import urllib.parse
import json
import os
import html
from datetime import datetime, timezone, timedelta


# Forex Factory weekly economic calendar
API_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

STATE_FILE = "sent_alerts.json"
IST = timezone(timedelta(hours=5, minutes=30))


def load_alerts():
    try:
        with open(STATE_FILE, "r") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_alerts(alerts):
    with open(STATE_FILE, "w") as file:
        json.dump(alerts, file, indent=2)


def send_telegram(message):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }).encode()

    request = urllib.request.Request(
        url,
        data=body,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read())

    if not result.get("ok"):
        print("Telegram error:", result)
        raise RuntimeError("Telegram did not accept the message")


def main():
    request = urllib.request.Request(
        API_URL,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        calendar = json.loads(response.read())

    if not isinstance(calendar, list):
        raise ValueError("Unexpected Forex Factory feed format")

    now = datetime.now(timezone.utc)
    alerts = load_alerts()

    print("Total events:", len(calendar))

    high_events = [
        event for event in calendar
        if str(event.get("impact", "")).lower() == "high"
    ]

    print("High-impact events:", len(high_events))

    for event in high_events:
        name = event.get("title") or "Unnamed event"
        currency = event.get("country") or "N/A"
        time_text = event.get("date")

        if not time_text:
            continue

        event_time = datetime.fromisoformat(
            time_text.replace("Z", "+00:00")
        )

        if event_time.tzinfo is None:
            event_time = event_time.replace(
                tzinfo=timezone.utc
            )

        event_time = event_time.astimezone(timezone.utc)

        # Ignore events that have already happened.
        if event_time <= now:
            continue

        event_key = (
            f"{currency}|{name}|{event_time.isoformat()}"
        )

        if event_key not in alerts:
            alerts[event_key] = {
                "detected": False,
                "1h": False,
                "30m": False
            }

        state = alerts[event_key]

        time_ist = event_time.astimezone(IST).strftime(
            "%d-%m-%Y %I:%M %p"
        )

        safe_name = html.escape(str(name))
        safe_currency = html.escape(str(currency))

        previous = html.escape(
            str(event.get("previous") or "Not available")
        )
        forecast = html.escape(
            str(event.get("forecast") or "Not available")
        )
        actual = html.escape(
            str(event.get("actual") or "Not available")
        )

        details = (
            f"🌍 Currency: {safe_currency}\n"
            f"📌 Event: <b>{safe_name}</b>\n"
            f"🕒 Time (IST): {time_ist}\n"
            f"📊 Previous: {previous}\n"
            f"🔮 Forecast: {forecast}\n"
            f"📈 Actual: {actual}\n"
        )

        source = (
            'Source: <a href="https://www.forexfactory.com/calendar">'
            'Forex Factory</a>'
        )

        # First detection alert.
        if not state["detected"]:
            send_telegram(
                "🔴 <b>HIGH-IMPACT ECONOMIC EVENT DETECTED</b>\n\n"
                f"{details}\n{source}"
            )

            state["detected"] = True
            save_alerts(alerts)

        minutes_left = (
            event_time - now
        ).total_seconds() / 60

        # One-hour reminder.
        if 55 <= minutes_left <= 65 and not state["1h"]:
            send_telegram(
                "⏰ <b>1-HOUR REMINDER</b>\n\n"
                f"{details}\n{source}"
            )

            state["1h"] = True
            save_alerts(alerts)

        # Thirty-minute reminder.
        if 25 <= minutes_left <= 35 and not state["30m"]:
            send_telegram(
                "⏰ <b>30-MINUTE REMINDER</b>\n\n"
                f"{details}\n{source}"
            )

            state["30m"] = True
            save_alerts(alerts)

    save_alerts(alerts)
    print("Forex Factory calendar checked successfully.")


if __name__ == "__main__":
    main()
