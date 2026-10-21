"""Telegram delivery. Credentials come from config (environment only)."""
import json
import urllib.parse
import urllib.request

import config as cfg


class TelegramNotifier:
    def __init__(self, token=None, chat_id=None):
        self.token = token or cfg.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or cfg.TELEGRAM_CHAT_ID

    def validate(self):
        if not self.token or not self.chat_id:
            raise RuntimeError(
                "Telegram secrets are missing. Set TELEGRAM_BOT_TOKEN and "
                "TELEGRAM_CHAT_ID (see .env.example)."
            )

    def send(self, message):
        self.validate()
        body = urllib.parse.urlencode({
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
            "link_preview_options": json.dumps({"is_disabled": True}),
        }).encode()
        request = urllib.request.Request(
            f"{cfg.TELEGRAM_API_BASE}/bot{self.token}/sendMessage", data=body
        )
        with urllib.request.urlopen(request, timeout=cfg.TELEGRAM_TIMEOUT) as r:
            result = json.loads(r.read().decode("utf-8"))
        if not result.get("ok"):
            raise RuntimeError(f"Telegram API error: {result}")
        print("TELEGRAM_SENT:", result.get("result", {}).get("message_id"))
