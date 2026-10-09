
import urllib.request
import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
import os
import urllib.parse

STATE_FILE = "sent_alerts.json"

try:
    with open(STATE_FILE, "r") as file:
        sent_alerts = json.load(file)
except (FileNotFoundError, json.JSONDecodeError):
    sent_alerts = {}

url = "https://www.financecalendar.com/wp-json/fc/v1/today"

request = urllib.request.Request(
    url,
    headers={"User-Agent": "Mozilla/5.0"}
)
response = urllib.request.urlopen(request)

data = json.loads(response.read())

high_impact_events = [
    event for event in data["events"]
    if event.get("impact") == "high"
]

ist_zone = timezone(timedelta(hours=5, minutes=30))

for event in high_impact_events:
    print("Event:", event.get("name"))

    time_text = event.get("time_utc")

    if time_text:
        utc_time = datetime.fromisoformat(
            time_text.replace("Z", "+00:00")
        )
        ist_time = utc_time.astimezone(ist_zone)
        print(
            "Time (IST):",
            ist_time.strftime("%d-%m-%Y %I:%M %p")
        )

token = os.environ["TELEGRAM_BOT_TOKEN"]
chat_id = os.environ["TELEGRAM_CHAT_ID"]

message = "✅ Economic calendar is connected to Telegram!"

url = f"https://api.telegram.org/bot{token}/sendMessage"

data_to_send = urllib.parse.urlencode({
    "chat_id": chat_id,
    "text": message
}).encode()

request = urllib.request.Request(
    url,
    data=data_to_send,
    headers={"User-Agent": "Mozilla/5.0"}
)

response = urllib.request.urlopen(request)
print("Telegram test message sent!")
