
import urllib.request
import json
from datetime import datetime, timezone, timedelta

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
