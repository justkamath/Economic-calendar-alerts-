"""What people can send the bot: /start /level /currencies /status /stop /help.

Telegram bots on GitHub Actions can't listen all the time, so each run (every
5 minutes) reads the messages sent since the last run and answers them.
"""
import html

import config as cfg
from sources.common import KNOWN_CURRENCIES

MAX_BATCHES = 5   # up to 500 new messages handled per run

LEVEL_TEXT = {
    "high": "🔴 High impact only",
    "medium": "🟠 Medium and High",
    "low": "🟡 Low, Medium and High",
}

KEYBOARD = {
    "keyboard": [
        [{"text": "/level high"}, {"text": "/level medium"}, {"text": "/level low"}],
        [{"text": "/currencies all"}, {"text": "/status"}, {"text": "/stop"}],
    ],
    "resize_keyboard": True,
}

HELP = (
    "<b>Economic Calendar Alerts</b>\n\n"
    "<b>Pick your level</b>\n"
    "/level high - 🔴 only the big ones\n"
    "/level medium - 🟠 medium and high\n"
    "/level low - 🟡 low, medium and high\n\n"
    "<b>Pick your currencies</b>\n"
    "/currencies USD EUR - only these\n"
    "/currencies all - every currency\n\n"
    "/status - see your settings\n"
    "/stop - pause alerts\n\n"
    "<i>Changes can take up to 5 minutes to apply.</i>"
)


def _currencies_text(cur):
    return "all currencies" if not cur else ", ".join(cur)


def _status(audience, chat_id):
    entry = audience.entries().get(chat_id)
    if entry is None:
        return "You are not subscribed. Send /start to begin."
    level, cur = entry
    return (f"<b>Your settings</b>\n{LEVEL_TEXT.get(level, level)}\n"
            f"💱 {html.escape(_currencies_text(cur))}")


def _subscribed(audience, chat_id):
    return chat_id in audience.entries() or chat_id in audience.owner_ids


def dispatch(chat_id, text, audience):
    """Handle one command and return the reply text."""
    store = audience.store
    parts = text.replace(",", " ").split()
    cmd = parts[0].split("@")[0].lower()
    args = [p for p in parts[1:]]
    chat_id = str(chat_id)

    if cmd in ("/start", "/help"):
        if cmd == "/help":
            return HELP
        if not _subscribed(audience, chat_id):
            code = getattr(cfg, "SIGNUP_CODE", "")
            if code and (not args or args[0] != code):
                return "🔒 This bot is invite-only. Send /start followed by your invite code."
            if not store.update(chat_id, level="high", currencies=None):
                return "Sorry, the subscriber limit has been reached."
        elif chat_id not in audience.entries():                    # resume after /stop
            if chat_id in audience.owner_ids:
                store.remove(chat_id)                              # back to the owner default
            else:
                store.update(chat_id, level="high", currencies=None)
        return f"✅ You're in.\n\n{_status(audience, chat_id)}\n\n{HELP}"

    if not _subscribed(audience, chat_id) and chat_id not in (store.subs if store else {}):
        return "Send /start first to subscribe."

    if cmd == "/level":
        level = args[0].lower() if args else ""
        if level not in LEVEL_TEXT:
            return "Please choose a level:\n/level high\n/level medium\n/level low"
        store.update(chat_id, level=level)
        return f"✅ Saved. You will receive:\n{LEVEL_TEXT[level]}"

    if cmd == "/currencies":
        if not args:
            return (f"💱 You receive: {html.escape(_currencies_text((audience.entries().get(chat_id) or (0, None))[1]))}\n\n"
                    f"Choose from: {', '.join(sorted(KNOWN_CURRENCIES))}\n"
                    "Example: /currencies USD EUR\nOr: /currencies all")
        if args[0].lower() == "all":
            store.update(chat_id, currencies=None)
            return "✅ Saved. You will receive all currencies."
        picked = []
        for item in args:
            code = item.upper()
            if code not in KNOWN_CURRENCIES:
                return (f"Sorry, I don't know that currency.\n"
                        f"Choose from: {', '.join(sorted(KNOWN_CURRENCIES))}")
            if code not in picked:
                picked.append(code)
        store.update(chat_id, currencies=picked)
        return f"✅ Saved. You will receive: {', '.join(picked)}"

    if cmd == "/status":
        return _status(audience, chat_id)

    if cmd == "/stop":
        if chat_id in audience.owner_ids:
            store.update(chat_id, level="off")      # owner is paused, not deleted
        else:
            store.remove(chat_id)
        return "🔕 Alerts paused. Send /start to turn them back on."

    return "Unknown command.\n\n" + HELP


def handle_updates(notifier, audience):
    """Read new messages sent to the bot and apply them. Never prints chat IDs."""
    store = audience.store
    handled = 0
    for _ in range(MAX_BATCHES):
        try:
            updates = notifier.get_updates(store.offset)
        except Exception as e:
            print("Could not read bot messages this run:", e)
            break
        if not updates:
            break
        for update in updates:
            store.set_offset(update["update_id"] + 1)
            message = update.get("message") or {}
            chat = message.get("chat") or {}
            text = (message.get("text") or "").strip()
            if chat.get("type") != "private" or not text.startswith("/"):
                continue
            try:
                reply = dispatch(chat["id"], text, audience)
            except Exception as e:
                print("Command failed:", type(e).__name__)
                continue
            handled += 1
            try:
                notifier.send(reply, chat_id=str(chat["id"]), reply_markup=KEYBOARD)
            except Exception as e:
                print("REPLY FAILED:", e)
        if len(updates) < 100:
            break
    print("Commands handled:", handled, "| subscribers:", len(store))
