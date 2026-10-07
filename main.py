import os
import urllib.parse
import urllib.request

token = os.environ["TELEGRAM_BOT_TOKEN"]
chat_id = os.environ["TELEGRAM_CHAT_ID"]

message = "🔔 Hello! Economic Calendar Alert System is working!"

url = f"https://api.telegram.org/bot{token}/sendMessage"

data = urllib.parse.urlencode({
    "chat_id": chat_id,
    "text": message
}).encode()

urllib.request.urlopen(url, data=data)

print("Telegram message sent successfully!")
