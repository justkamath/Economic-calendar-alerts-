import urllib.request
import json

url = "https://www.financecalendar.com/wp-json/fc/v1/today"

response = urllib.request.urlopen(url)
data = json.loads(response.read())

print("Economic calendar data received!")
print(data)
