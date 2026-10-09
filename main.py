import urllib.request
import json
from datetime import datetime, timezone, timedelta

url = "https://www.financecalendar.com/wp-json/fc/v1/today"

request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
response = urllib.request.urlopen(request)

data = json.loads(response.read())
high_impact_events = [
    event for event in data["events"]
    if event.get("impact") == "high"
]

for event in high_impact_events:
    print("Event:", event.get("name"))
    utc_time = datetime.fromisoformat(event.get("time_utc").replace("Z", "+00:00"))
ist_time = utc_time.astimezone(timezone(timedelta(hours=5, minutes=30)))
print("Time (IST):", ist_time.strftime("%d-%m-%Y %I:%M %p"))
