"""Telegram delivery. Credentials come from config (environment only).

Note: chat IDs are never printed - GitHub Actions logs can be public.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

import config as cfg


class TelegramError(RuntimeError):
    def __init__(self, code, description):
        super().__init__(f"Telegram error {code}: {description}")
        self.code = code
        self.description = description


def parse_chat_ids(value):
    """'111, -100222' -> ['111', '-100222'] (duplicates and blanks dropped)."""
    seen = []
    for part in str(value or "").replace(";", ",").replace("\n", ",").split(","):
        part = part.strip()
        if part and part not in seen:
            seen.append(part)
    return seen


class TelegramNotifier:
    def __init__(self, token=None, chat_id=None):
        self.token = token or cfg.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or cfg.TELEGRAM_CHAT_ID
        self.chat_ids = parse_chat_ids(self.chat_id)

    def validate(self):
        if not self.token or not self.chat_ids:
            raise RuntimeError(
                "Telegram secrets are missing. Set TELEGRAM_BOT_TOKEN and "
                "TELEGRAM_CHAT_ID (see .env.example)."
            )

    def _call(self, method, params):
        request = urllib.request.Request(
            f"{cfg.TELEGRAM_API_BASE}/bot{self.token}/{method}",
            data=urllib.parse.urlencode(params).encode(),
        )
        try:
            with urllib.request.urlopen(request, timeout=cfg.TELEGRAM_TIMEOUT) as r:
                result = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            description = ""
            try:
                description = json.loads(e.read().decode("utf-8")).get("description", "")
            except Exception:
                pass
            raise TelegramError(e.code, description or "HTTP error") from None
        if not result.get("ok"):
            raise TelegramError(result.get("error_code", 0),
                                result.get("description", "unknown"))
        return result.get("result")

    def send(self, message, chat_id=None, reply_markup=None):
        """Send an HTML message to every chat (your own ID, a group, friends).

        One chat failing (for example a friend who blocked the bot) never stops
        the others. It only counts as a failure if NO chat received it, so the
        alert is retried on the next run instead of being marked as sent.
        """
        self.validate()
        targets = [str(chat_id)] if chat_id else self.chat_ids
        delivered, last_error = 0, None
        for number, target in enumerate(targets, start=1):
            params = {
                "chat_id": target,
                "text": message,
                "parse_mode": "HTML",
                "link_preview_options": json.dumps({"is_disabled": True}),
            }
            if reply_markup:
                params["reply_markup"] = json.dumps(reply_markup)
            try:
                result = self._call("sendMessage", params)
            except TelegramError as error:
                last_error = error
                print(f"TELEGRAM_FAILED: chat #{number}: {error}")  # never the ID
                continue
            delivered += 1
            print("TELEGRAM_SENT:", (result or {}).get("message_id"))
        if delivered == 0 and last_error is not None:
            raise last_error
        if len(targets) > 1:
            print(f"Delivered to {delivered} of {len(targets)} chats")

    def set_commands(self, commands):
        """Fill the bot's Menu list. `commands` is [(name, description), ...]."""
        self._call("setMyCommands", {"commands": json.dumps(
            [{"command": name, "description": text} for name, text in commands])})

    def get_updates(self, offset=0, limit=100):
        """Fetch new messages sent to the bot (no long-polling)."""
        return self._call("getUpdates", {
            "offset": offset,
            "limit": limit,
            "timeout": 0,
            "allowed_updates": json.dumps(["message"]),
        }) or []
