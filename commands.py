"""What people see and tap when they talk to the bot.

Everything works with buttons (no typing needed), and the old typed commands
/start /level /currencies /status /stop /help still work too.

Telegram bots on GitHub Actions can't listen all the time, so each run (every
5 minutes) reads the messages sent since the last run and answers them.
"""
import html
import re

import config as cfg
from sources.common import KNOWN_CURRENCIES
from summary import FLAGS

MAX_BATCHES = 5   # up to 500 new messages handled per run
ORDER = ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "CNY")

LEVEL_TEXT = {
    "high": "🔥 Big moves only (high impact)",
    "medium": "⚡ Balanced (medium + high)",
    "low": "🫧 All of it (low, medium + high)",
}
LEVEL_BUTTON = {"high": "🔥 Big moves", "medium": "⚡ Balanced", "low": "🫧 All of it"}

TICK = " ✓"
BTN_CURRENCIES, BTN_SETTINGS = "💸 Currencies", "🎯 My setup"
BTN_HELP, BTN_PAUSE, BTN_START = "🧠 How it works", "😴 Pause", "▶️ Start"
BTN_ALL, BTN_BACK = "🌍 All currencies", "⬅️ Back"

REMINDERS = ("👀 A heads-up when it hits the calendar\n"
             "⏰ A nudge 1 hour before\n"
             "⏳ A last call 30 min before")

HELP = (
    "<b>🧠 How it works</b>\n"
    "<i>Market news, zero noise.</i>\n\n"
    "<b>🚦 Pick your vibe</b>\n"
    "🔥 <b>Big moves</b>: Only the market shakers\n"
    "⚡ <b>Balanced</b>: The sweet spot (most people pick this)\n"
    "🫧 <b>All of it</b>: Every release, even the small ones\n\n"
    "<b>💸 Pick your currencies</b>\n"
    "Tap <b>Currencies</b>, then tap the flags you follow. "
    "Tap again to drop one. None picked = you get everything.\n\n"
    "<b>🔔 How we ping you</b>\n"
    f"{REMINDERS}\n\n"
    "✍️ <b>Prefer typing?</b>\n\n"
    "<b>Impact</b>\n"
    "/level high\n"
    "/level medium\n"
    "/level low\n\n"
    "<b>Currencies</b> (any mix, spaces or commas)\n"
    "/currencies USD EUR\n"
    "<i>Only the ones you list</i>\n"
    "/currencies all\n"
    "<i>Every currency, no filter</i>\n\n"
    "<b>Your account</b>\n"
    "/status\n"
    "<i>See your setup</i>\n"
    "/stop\n"
    "<i>Pause alerts</i>\n\n"
    "🏳️ <b>Codes</b>\n"
    "🇺🇸 USD  🇪🇺 EUR  🇬🇧 GBP\n"
    "🇯🇵 JPY  🇦🇺 AUD  🇳🇿 NZD\n"
    "🇨🇦 CAD  🇨🇭 CHF  🇨🇳 CNY\n\n"
    "<i>Changes kick in within 5 min.</i>"
)


# ---------- small helpers ----------
def _flag_codes(cur):
    return "  ".join(f"{FLAGS.get(c, '🌐')} {c}" for c in cur)


def _currencies_text(cur):
    return "🌍 All of them" if not cur else _flag_codes(cur)


def _level_text(level):
    return LEVEL_TEXT.get(level, "⚪ Everything")


def _entry(audience, chat_id):
    return audience.entries().get(chat_id)


def _subscribed(audience, chat_id):
    return chat_id in audience.entries() or chat_id in audience.owner_ids


def _card(level, cur, title="🎯 Your setup"):
    return (f"<b>{title}</b>\n\n"
            f"<b>🚦 Your vibe</b>\n{_level_text(level)}\n\n"
            f"<b>💸 Your currencies</b>\n{_currencies_text(cur)}\n\n"
            f"<b>🔔 How we ping you</b>\n{REMINDERS}")


# ---------- keyboards ----------
def main_keyboard(level=None):
    return {"keyboard": [
        [{"text": LEVEL_BUTTON[name]} for name in ("high", "medium", "low")],
        [{"text": BTN_CURRENCIES}, {"text": BTN_SETTINGS}],
        [{"text": BTN_HELP}, {"text": BTN_PAUSE}],
    ], "resize_keyboard": True, "is_persistent": True}


def currency_keyboard(cur):
    chosen = set(cur or ())

    def button(code):
        return {"text": f"{FLAGS.get(code, '🌐')} {code}" + (TICK if code in chosen else "")}
    rows = [[button(c) for c in ORDER[i:i + 3]] for i in range(0, len(ORDER), 3)]
    rows.append([{"text": BTN_ALL + (TICK if not chosen else "")}, {"text": BTN_BACK}])
    return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True}


START_KEYBOARD = {"keyboard": [[{"text": BTN_START}]], "resize_keyboard": True}
KEYBOARD = main_keyboard("high")   # kept for older callers


# ---------- reading what the person sent ----------
def parse(text):
    """(command, args) for a typed command or a tapped button; (None, []) if unknown."""
    raw = (text or "").strip()
    if raw.startswith("/"):
        parts = raw.replace(",", " ").split()
        return parts[0].split("@")[0].lower(), parts[1:]
    plain = raw.replace(TICK.strip(), "").replace("✅", "").strip()
    low = plain.lower()
    for level, label in LEVEL_BUTTON.items():
        if plain == label:
            return "/level", [level]
    if "all currencies" in low:
        return "/currencies", ["all"]
    if "currencies" in low:
        return "/currencies", []
    if "my setup" in low or "my settings" in low:
        return "/status", []
    if "how it works" in low or low == "help":
        return "/help", []
    if "pause" in low:
        return "/stop", []
    if "back" in low:
        return "/back", []
    if low.endswith("start") or low == "start":
        return "/start", []
    match = re.search(r"\b([A-Z]{3})\b", plain)
    if match and match.group(1) in KNOWN_CURRENCIES:
        return "/toggle", [match.group(1)]
    return None, []


# ---------- answering ----------
def respond(chat_id, text, audience):
    """Handle one message. Returns (reply text, keyboard)."""
    store = audience.store
    cmd, args = parse(text)
    chat_id = str(chat_id)
    entry = _entry(audience, chat_id)

    def keys(kind="main"):
        now = _entry(audience, chat_id)
        if now is None:
            return START_KEYBOARD
        return currency_keyboard(now[1]) if kind == "currencies" else main_keyboard(now[0])

    if cmd == "/start":
        if not _subscribed(audience, chat_id):
            code = getattr(cfg, "SIGNUP_CODE", "")
            if code and (not args or args[0] != code):
                return ("🔒 <b>Invite only</b>\n"
                        "Got a code? Send <code>/start</code> then your code.", START_KEYBOARD)
            if not store.update(chat_id, level="high", currencies=None):
                return "😅 We're full right now: the subscriber limit has been reached.", START_KEYBOARD
        elif entry is None:                                        # resume after /stop
            if chat_id in audience.owner_ids:
                store.remove(chat_id)                              # back to the owner default
            else:
                store.update(chat_id, level="high", currencies=None)
        level, cur = _entry(audience, chat_id)
        return (f"<b>You're in ✨</b>\n"
                "<i>Market news, zero noise. We ping, you chill.</i>\n\n"
                f"{_card(level, cur, '🎯 Your setup')}\n\n"
                "Tap the buttons below to make it yours 👇\n"
                "<i>Changes kick in within 5 min.</i>"), keys()

    if cmd == "/help":
        return HELP, keys()

    if not _subscribed(audience, chat_id) and (store is None or chat_id not in store.subs):
        if cmd is None and not (text or "").startswith("/"):
            return "👋 Hey! Tap <b>▶️ Start</b> to get your alerts.", START_KEYBOARD
        return "👋 Send /start first (or tap ▶️ Start) to join.", START_KEYBOARD

    if cmd == "/level":
        level = args[0].lower() if args else ""
        if level not in LEVEL_TEXT:
            return "Pick a vibe with the buttons below 👇", keys()
        store.update(chat_id, level=level)
        return (f"<b>Locked in ✅</b>\n{_level_text(level)}\n\n"
                "<i>Kicks in within 5 min.</i>"), keys()

    if cmd == "/currencies":
        if not args:
            cur = (entry or (None, None))[1]
            return ("<b>💸 Pick your currencies</b>\n\n"
                    f"Right now: {_currencies_text(cur)}\n\n"
                    "Tap a flag to add it, tap again to drop it.\n"
                    "<i>None picked = you get everything.</i>"), keys("currencies")
        if args[0].lower() == "all":
            store.update(chat_id, currencies=None)
            return ("<b>Locked in ✅</b>\n🌍 all currencies\n\n"
                    "<i>Kicks in within 5 min.</i>"), keys("currencies")
        picked = []
        for item in args:
            code = item.upper()
            if code not in KNOWN_CURRENCIES:
                return ("🤔 Don't know that currency.\n"
                        f"Pick from: {', '.join(ORDER)}"), keys("currencies")
            if code not in picked:
                picked.append(code)
        store.update(chat_id, currencies=picked)
        return (f"<b>Locked in ✅</b>\n{_flag_codes(picked)}\n\n"
                "<i>Kicks in within 5 min.</i>"), keys("currencies")

    if cmd == "/toggle":
        code = args[0]
        current = list((entry or (None, None))[1] or [])
        if code in current:
            current.remove(code)
            note = f"➖ {FLAGS.get(code, '🌐')} {code} is out"
        else:
            current.append(code)
            note = f"➕ {FLAGS.get(code, '🌐')} {code} is in"
        current.sort(key=ORDER.index)
        store.update(chat_id, currencies=current or None)
        return (f"{note}\n\nRight now: {_currencies_text(current or None)}\n\n"
                "<i>Kicks in within 5 min.</i>"), keys("currencies")

    if cmd in ("/status", "/back"):
        if entry is None:
            return "😴 <b>You're on pause</b>\nTap <b>▶️ Start</b> when you're back.", START_KEYBOARD
        return _card(entry[0], entry[1]), keys()

    if cmd == "/stop":
        if chat_id in audience.owner_ids:
            store.update(chat_id, level="off")      # owner is paused, not deleted
        else:
            store.remove(chat_id)
        return ("😴 <b>Paused</b>\n"
                "We've gone quiet. Tap <b>▶️ Start</b> whenever you want back in."), START_KEYBOARD

    if (text or "").startswith("/"):
        return "🤷 Not a command I know.\n\n" + HELP, keys()
    return "Tap a button below 👇 or hit <b>🧠 How it works</b>.", keys()


def dispatch(chat_id, text, audience):
    """The reply text only (kept for older callers and tests)."""
    return respond(chat_id, text, audience)[0]


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
            if chat.get("type") != "private" or not text:
                continue
            try:
                reply, keyboard = respond(chat["id"], text, audience)
            except Exception as e:
                print("Command failed:", type(e).__name__)
                continue
            handled += 1
            try:
                notifier.send(reply, chat_id=str(chat["id"]), reply_markup=keyboard)
            except Exception as e:
                print("REPLY FAILED:", e)
        if len(updates) < 100:
            break
    print("Commands handled:", handled, "| subscribers:", len(store))
