import urllib.request
import json

url = "https://www.financecalendar.com/wp-json/fc/v1/today"

request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
response = urllib.request.urlopen(request)

data = json.loads(response.read())
for event in data["events"]:
    print(event.get("time_utc"), "|", event.get("name"), "| impact:", event.get("impact"))
