
import urllib.request
import urllib.parse
import json
import os
from datetime import datetime, timezone, timedelta

API_URL = "https://www.financecalendar.com/wp-json/fc/v1/today"
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
        "text": message
    }).encode()

    request = urllib.request.Request(
        url,
        data=body,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read())

    if not result.get("ok"):
        raise RuntimeError("Telegram did not accept the message")


def main():
    request = urllib.request.Request(
        API_URL,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        calendar = json.loads(response.read())

    now = datetime.now(timezone.utc)
    alerts = load_alerts()

    
    events = calendar.get("events", [])
    print("Total events:", len(events))
    print("High-impact events:", sum(
        1 for event in events
        if event.get("impact", "").lower() == "high"
    ))

    for event in events:

        if event.get("impact", "").lower() != "high":
            continue

        name = event.get("name", "Unnamed event")
        time_text = event.get("time_utc")
        if not time_text:
            continue

        event_time = datetime.fromisoformat(
            time_text.replace("Z", "+00:00")
        )
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=timezone.utc)

        event_time = event_time.astimezone(timezone.utc)

        # Ignore events that have already happened.
        if event_time <= now:
            continue

        event_key = f"{name}|{event_time.isoformat()}"
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

        # Alert once when the event is first detected.
        if not state["detected"]:
            send_telegram(
                f"🔴 HIGH-IMPACT ECONOMIC EVENT DETECTED\n\n"
                f"📌 {name}\n"
                f"🕒 Time (IST): {time_ist}\n\n"
                f"Source: financecalendar.com"
            )
            state["detected"] = True
            save_alerts(alerts)

        minutes_left = (event_time - now).total_seconds() / 60

        # Reminder windows tolerate some GitHub Actions scheduling delay.
        if 55 <= minutes_left <= 65 and not state["1h"]:
            send_telegram(
                f"⏰ 1-HOUR REMINDER\n\n"
                f"📌 {name}\n"
                f"🕒 Time (IST): {time_ist}\n\n"
                f"Source: financecalendar.com"
            )
            state["1h"] = True
            save_alerts(alerts)

        if 25 <= minutes_left <= 35 and not state["30m"]:
            send_telegram(
                f"⏰ 30-MINUTE REMINDER\n\n"
                f"📌 {name}\n"
                f"🕒 Time (IST): {time_ist}\n\n"
                f"Source: financecalendar.com"
            )
            state["30m"] = True
            save_alerts(alerts)

    save_alerts(alerts)
    print("Calendar checked successfully.")


if __name__ == "__main__":
    main()
