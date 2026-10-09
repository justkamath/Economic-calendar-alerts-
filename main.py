import urllib.request
import json

url = "https://www.financecalendar.com/wp-json/fc/v1/today"

request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
response = urllib.request.urlopen(request)

data = json.loads(response.read())
high_impact_events = [
    event for event in data["events"]
    if event.get("impact") == "high"
]

print("High-impact events:", len(high_impact_events))
