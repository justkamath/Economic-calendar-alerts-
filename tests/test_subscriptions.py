"""Per-person levels and currencies.
Run from the repo root:  python -m unittest discover -s tests -v"""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alerts  # noqa: E402
import commands  # noqa: E402
import config as cfg  # noqa: E402
import state as state_mod  # noqa: E402
import subscribers as subs  # noqa: E402
from subscribers import Audience, SubscriberError, SubscriberStore  # noqa: E402

KEY = "a-long-random-secret-key-123456"
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=cfg.UTC)


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


class CryptoTests(unittest.TestCase):
    def test_round_trip(self):
        blob = subs.encrypt(KEY, b'{"hello": "world"}')
        self.assertEqual(subs.decrypt(KEY, blob), b'{"hello": "world"}')

    def test_ciphertext_hides_the_content_and_differs_each_time(self):
        a, b = subs.encrypt(KEY, b"123456789"), subs.encrypt(KEY, b"123456789")
        self.assertNotEqual(a, b)
        self.assertNotIn(b"123456789", a)

    def test_wrong_key_is_rejected(self):
        blob = subs.encrypt(KEY, b"data")
        with self.assertRaises(SubscriberError):
            subs.decrypt("another-long-random-secret-key-99", blob)

    def test_tampering_and_garbage_are_rejected(self):
        blob = bytearray(subs.encrypt(KEY, b"data"))
        blob[-3] = ord("A") if blob[-3] != ord("A") else ord("B")
        for bad in (bytes(blob), b"junk", b""):
            with self.assertRaises(SubscriberError):
                subs.decrypt(KEY, bad)

    def test_short_key_is_rejected(self):
        with self.assertRaises(SubscriberError):
            subs.encrypt("short", b"x")

    def test_long_messages_work(self):
        data = os.urandom(5000)
        self.assertEqual(subs.decrypt(KEY, subs.encrypt(KEY, data)), data)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "subscribers.enc")

    def store(self, **kw):
        return SubscriberStore.load(self.path, KEY, kw.get("max", 5))

    def test_save_and_reload(self):
        s = self.store()
        s.update("1", level="medium", currencies=["USD", "EUR"])
        s.update("2", level="high")
        s.set_offset(42)
        s.save()
        again = self.store()
        self.assertEqual(again.offset, 42)
        self.assertEqual(again.get("1"), {"level": "medium", "cur": ["USD", "EUR"]})
        self.assertEqual(again.get("2"), {"level": "high", "cur": None})

    def test_file_never_contains_readable_ids(self):
        s = self.store()
        s.update("987654321", level="high")
        s.save()
        with open(self.path, "rb") as f:
            self.assertNotIn(b"987654321", f.read())

    def test_nothing_written_when_nothing_changed(self):
        self.store().save()
        self.assertFalse(os.path.exists(self.path))

    def test_limit_blocks_new_signups_but_not_changes(self):
        s = self.store(max=2)
        self.assertTrue(s.update("1", level="high"))
        self.assertTrue(s.update("2", level="high"))
        self.assertFalse(s.update("3", level="high"))
        self.assertTrue(s.update("1", level="low"))

    def test_changing_level_keeps_currencies_and_back(self):
        s = self.store()
        s.update("1", level="high", currencies=["USD"])
        s.update("1", level="low")
        self.assertEqual(s.get("1"), {"level": "low", "cur": ["USD"]})
        s.update("1", currencies=None)
        self.assertEqual(s.get("1"), {"level": "low", "cur": None})

    def test_wrong_key_does_not_overwrite_the_file(self):
        s = self.store()
        s.update("1", level="high")
        s.save()
        before = open(self.path, "rb").read()
        with self.assertRaises(SubscriberError):
            SubscriberStore.load(self.path, "another-long-random-secret-key-99", 5)
        self.assertEqual(open(self.path, "rb").read(), before)

    def test_remove(self):
        s = self.store()
        s.update("1", level="high")
        self.assertTrue(s.remove("1"))
        self.assertFalse(s.remove("1"))


class AudienceTests(unittest.TestCase):
    def audience(self, owner=("100",), **people):
        store = SubscriberStore("x", KEY, 50)
        for cid, (level, cur) in people.items():
            store.subs[cid.lstrip("c")] = {"level": level, "cur": cur}
        return Audience(owner, store, default_level="medium")

    def test_owner_gets_default_level_all_currencies(self):
        a = self.audience()
        self.assertEqual(a.recipients_for(2, "JPY"), ["100"])
        self.assertEqual(a.recipients_for(1, "JPY"), [])

    def test_levels_decide_who_gets_what(self):
        a = self.audience(c1=("high", None), c2=("low", None))
        self.assertEqual(sorted(a.recipients_for(3, "USD")), ["1", "100", "2"])
        self.assertEqual(sorted(a.recipients_for(2, "USD")), ["100", "2"])
        self.assertEqual(sorted(a.recipients_for(1, "USD")), ["2"])

    def test_currency_filter(self):
        a = self.audience(c1=("high", ["USD", "EUR"]))
        self.assertIn("1", a.recipients_for(3, "EUR"))
        self.assertNotIn("1", a.recipients_for(3, "JPY"))
        self.assertIn("1", a.recipients_for(3, "usd"))

    def test_own_settings_override_owner_default_and_off_pauses(self):
        a = self.audience(c100=("off", None))
        self.assertEqual(a.recipients_for(3, "USD"), [])
        b = self.audience(c100=("high", ["GBP"]))
        self.assertEqual(b.recipients_for(3, "GBP"), ["100"])
        self.assertEqual(b.recipients_for(3, "USD"), [])

    def test_min_rank(self):
        self.assertEqual(self.audience().min_rank(), 2)
        self.assertEqual(self.audience(c1=("low", None)).min_rank(), 1)
        self.assertEqual(self.audience(owner=()).min_rank(), len(cfg.IMPACT_RANK))

    def test_blocked_subscribers_are_removed_but_never_owners(self):
        a = self.audience(c1=("high", None))
        a.remove("1")
        a.remove("100")
        self.assertIsNone(a.store.get("1"))
        self.assertEqual(a.owner_ids, ["100"])


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.store = SubscriberStore("x", KEY, 3)
        self.aud = Audience(["100"], self.store, default_level="medium")

    def say(self, chat, text):
        return commands.dispatch(chat, text, self.aud)

    def test_start_subscribes_at_high_for_everything(self):
        self.assertIn("You're in", self.say(7, "/start"))
        self.assertEqual(self.store.get("7"), {"level": "high", "cur": None})

    def test_level_and_currencies(self):
        self.say(7, "/start")
        self.assertIn("Locked in", self.say(7, "/level medium"))
        self.assertIn("Locked in", self.say(7, "/currencies usd, eur"))
        self.assertEqual(self.store.get("7"), {"level": "medium", "cur": ["USD", "EUR"]})
        self.say(7, "/currencies all")
        self.assertIsNone(self.store.get("7")["cur"])

    def test_bad_input_changes_nothing(self):
        self.say(7, "/start")
        self.assertIn("Pick a vibe", self.say(7, "/level banana"))
        self.assertIn("Don't know that currency", self.say(7, "/currencies USD XYZ"))
        self.assertEqual(self.store.get("7"), {"level": "high", "cur": None})

    def test_commands_need_start_first(self):
        self.assertIn("/start first", self.say(8, "/level low"))
        self.assertIsNone(self.store.get("8"))

    def test_stop_removes_and_start_returns(self):
        self.say(7, "/start")
        self.assertIn("Paused", self.say(7, "/stop"))
        self.assertIsNone(self.store.get("7"))
        self.assertIn("You're in", self.say(7, "/start"))

    def test_owner_can_tune_pause_and_resume(self):
        self.say(100, "/level high")
        self.assertEqual(self.aud.entries()["100"][0], "high")
        self.say(100, "/stop")
        self.assertNotIn("100", self.aud.entries())
        self.say(100, "/start")
        self.assertEqual(self.aud.entries()["100"], ("medium", None))

    def test_signup_limit(self):
        for i in (1, 2, 3):
            self.say(i, "/start")
        self.assertIn("limit", self.say(4, "/start"))

    def test_invite_code(self):
        with mock.patch.object(cfg, "SIGNUP_CODE", "sesame", create=True):
            self.assertIn("Invite only", self.say(7, "/start"))
            self.assertIn("Invite only", self.say(7, "/start wrong"))
            self.assertIsNone(self.store.get("7"))
            self.assertIn("You're in", self.say(7, "/start sesame"))
            self.assertIn("You're in", self.say(100, "/start"))   # owner needs no code

    def test_status_and_unknown_and_botname_suffix(self):
        self.say(7, "/start")
        self.say(7, "/currencies GBP")
        status = self.say(7, "/status@MyBot")
        self.assertIn("GBP", status)
        self.assertIn("Not a command", self.say(7, "/dance"))


class ButtonTests(unittest.TestCase):
    def setUp(self):
        self.store = SubscriberStore("x", KEY, 5)
        self.aud = Audience(["100"], self.store, default_level="medium")

    def tap(self, text, chat=7):
        return commands.respond(chat, text, self.aud)

    def texts(self, keyboard):
        return [b["text"] for row in keyboard["keyboard"] for b in row]

    def test_every_button_is_understood(self):
        self.tap("/start")
        for kb in (commands.main_keyboard("medium"), commands.currency_keyboard(["USD"]),
                   commands.currency_keyboard(None), commands.START_KEYBOARD):
            for label in self.texts(kb):
                self.assertIsNotNone(commands.parse(label)[0], label)

    def test_start_button_subscribes(self):
        reply, keyboard = self.tap("▶️ Start")
        self.assertIn("You're in", reply)
        self.assertIn("🎯 Sharp ✓", self.texts(keyboard))

    def test_level_buttons_save_and_show_the_tick(self):
        self.tap("/start")
        reply, keyboard = self.tap("⚡ Core")
        self.assertEqual(self.store.get("7")["level"], "medium")
        self.assertIn("⚡ Core ✓", self.texts(keyboard))
        self.assertIn("🎯 Sharp", self.texts(keyboard))
        self.assertNotIn("🎯 Sharp ✓", self.texts(keyboard))
        self.tap("🌊 Flood ✓")                       # older button labels still work
        self.assertEqual(self.store.get("7")["level"], "low")

    def test_currency_buttons_toggle(self):
        self.tap("/start")
        reply, keyboard = self.tap("💸 Currencies")
        self.assertIn("🌍 All currencies ✓", self.texts(keyboard))
        self.tap("🇺🇸 USD")
        reply, keyboard = self.tap("🇪🇺 EUR")
        self.assertEqual(self.store.get("7")["cur"], ["USD", "EUR"])
        self.assertIn("🇺🇸 USD ✓", self.texts(keyboard))
        self.assertIn("🇪🇺 EUR ✓", self.texts(keyboard))
        self.assertIn("is out", self.tap("🇺🇸 USD ✓")[0])
        self.assertEqual(self.store.get("7")["cur"], ["EUR"])
        self.tap("🇪🇺 EUR ✓")                      # removing the last one means "all"
        self.assertIsNone(self.store.get("7")["cur"])

    def test_all_currencies_and_back(self):
        self.tap("/start")
        self.tap("🇬🇧 GBP")
        self.tap("🌍 All currencies")
        self.assertIsNone(self.store.get("7")["cur"])
        reply, keyboard = self.tap("⬅️ Back")
        self.assertIn("buttons", reply)
        self.assertIn("🧭 My setup", self.texts(keyboard))

    def test_pause_then_start_again(self):
        self.tap("/start")
        reply, keyboard = self.tap("😴 Pause")
        self.assertIn("Paused", reply)
        self.assertEqual(self.texts(keyboard), ["▶️ Start"])
        self.assertIsNone(self.store.get("7"))
        self.assertIn("You're in", self.tap("▶️ Start")[0])

    def test_settings_card_shows_flags_and_reminders(self):
        self.tap("/start")
        self.tap("🇯🇵 JPY")
        reply, _ = self.tap("🎯 My setup")
        for part in ("🇯🇵 JPY", "Your vibe", "Your currencies", "1 hour before", "30 min before"):
            self.assertIn(part, reply)

    def test_help_and_unknown_text(self):
        self.tap("/start")
        self.assertIn("How it works", self.tap("🧠 How it works")[0])
        self.assertIn("/menu", self.tap("random words")[0])

    def test_messages_are_valid_telegram_html_and_short(self):
        self.tap("/start")
        for text in ("/start", "/help", "🎯 My setup", "💸 Currencies", "🇺🇸 USD", "😴 Pause"):
            reply, _ = self.tap(text)
            self.assertLess(len(reply), 1200)
            for tag in ("b", "i"):
                self.assertEqual(reply.count(f"<{tag}>"), reply.count(f"</{tag}>"), text)

    def test_sentence_case_and_typing_block(self):
        self.tap("/start")
        help_text = self.tap("🧠 How it works")[0]
        self.assertIn("✍️ <b>Prefer typing?</b>", help_text)
        lines = help_text.split("\n")
        for command in ("/level high", "/level medium", "/level low", "/currencies USD EUR",
                        "/currencies all", "/status", "/stop"):
            self.assertIn(command, lines)             # each command sits alone on its line
        self.assertIn("🇺🇸 USD  🇪🇺 EUR  🇬🇧 GBP", lines)
        self.assertLess(len(help_text), 3000)
        for text in ("/start", "💸 Currencies", "🇺🇸 USD", "😴 Pause"):
            reply = self.tap(text)[0]
            body = reply.replace("<b>", "").replace("<i>", "")
            for word in ("you're in", "tap the", "right now", "kicks in", "paused"):
                self.assertNotIn("\n" + word, "\n" + body, text)    # no lines starting lowercase

    def test_ticks_live_on_the_buttons_not_in_messages(self):
        self.tap("/start")
        self.tap("🇺🇸 USD")
        for text in ("🎯 My setup", "💸 Currencies"):
            self.assertNotIn("✓", self.tap(text)[0])

    def test_buttons_collapse_after_a_tap_but_currency_picking_stays_open(self):
        _, keyboard = self.tap("/start")
        self.assertTrue(keyboard.get("one_time_keyboard"))
        _, picker = self.tap("💸 Currencies")
        self.assertFalse(picker.get("one_time_keyboard"))
        self.assertTrue(picker.get("is_persistent"))

    def test_reading_replies_leave_the_bar_alone(self):
        self.tap("/start")
        for text in ("🧭 My setup", "🧠 How it works", "/status", "/help", "random words"):
            self.assertIsNone(self.tap(text)[1], text)

    def test_menu_command_brings_the_buttons_back_with_current_ticks(self):
        self.tap("/start")
        self.tap("🌊 Flood")
        reply, keyboard = self.tap("/menu")
        self.assertIn("buttons", reply)
        self.assertIn("🌊 Flood ✓", self.texts(keyboard))

    def test_setup_and_pause_are_aliases(self):
        self.tap("/start")
        self.assertIn("Your setup", self.tap("/setup")[0])
        self.assertIn("Paused", self.tap("/pause")[0])

    def test_old_level_names_still_work(self):
        self.tap("/start")
        for label, level in (("🔥 High", "high"), ("⚡ Balanced", "medium"), ("🫧 All of it", "low"),
                             ("/level low", "low"), ("🎯 Sharp", "high")):
            self.tap(label)
            self.assertEqual(self.store.get("7")["level"], level, label)

    def test_every_menu_command_is_valid_for_telegram(self):
        for name, description in commands.BOT_COMMANDS:
            self.assertRegex(name, r"^[a-z0-9_]{1,32}$")
            self.assertTrue(1 <= len(description) <= 256)
        for name, _ in commands.BOT_COMMANDS:
            self.assertIsNotNone(commands.parse("/" + name)[0])

    def test_help_explains_how_to_get_the_buttons_back(self):
        self.assertIn("/menu", commands.HELP)

    def test_strangers_are_asked_to_start(self):
        reply, keyboard = self.tap("😴 Pause", chat=55)
        self.assertIn("/start first", reply)
        self.assertEqual(self.texts(keyboard), ["▶️ Start"])


class FakeBot:
    def __init__(self, updates=()):
        self.updates, self.sent = list(updates), []

    def get_updates(self, offset=0, limit=100):
        out, self.updates = [u for u in self.updates if u["update_id"] >= offset], []
        return out

    def send(self, message, chat_id=None, reply_markup=None):
        self.sent.append((chat_id, message))


def update(uid, chat, text, kind="private"):
    return {"update_id": uid, "message": {"chat": {"id": chat, "type": kind}, "text": text}}


class HandleUpdatesTests(unittest.TestCase):
    def test_commands_are_answered_and_offset_advances(self):
        store = SubscriberStore("x", KEY, 10)
        aud = Audience(["100"], store)
        bot = FakeBot([update(5, 7, "/start"), update(6, 7, "/level low")])
        quiet(commands.handle_updates, bot, aud)
        self.assertEqual(store.offset, 7)
        self.assertEqual(store.get("7")["level"], "low")
        self.assertEqual([c for c, _ in bot.sent], ["7", "7"])

    def test_groups_are_ignored_and_strangers_get_a_nudge(self):
        store = SubscriberStore("x", KEY, 10)
        bot = FakeBot([update(1, -5, "/start", "group"), update(2, 7, "hello")])
        quiet(commands.handle_updates, bot, Audience(["100"], store))
        self.assertEqual(len(store), 0)
        self.assertEqual([c for c, _ in bot.sent], ["7"])      # a gentle nudge, not silence
        self.assertIn("Start", bot.sent[0][1])
        self.assertEqual(store.offset, 3)

    def test_reading_failure_is_survived(self):
        class Broken(FakeBot):
            def get_updates(self, *a, **k):
                raise RuntimeError("409 webhook")
        quiet(commands.handle_updates, Broken(), Audience(["100"], SubscriberStore("x", KEY, 3)))


class MenuListTests(unittest.TestCase):
    def test_menu_is_published_once_and_remembered(self):
        class Bot(FakeBot):
            published = []

            def set_commands(self, items):
                self.published.append(items)
        store = SubscriberStore("x", KEY, 5)
        aud = Audience(["100"], store)
        bot = Bot()
        quiet(commands.handle_updates, bot, aud)
        quiet(commands.handle_updates, bot, aud)
        self.assertEqual(len(bot.published), 1)
        self.assertEqual(store.menu_version, commands.MENU_VERSION)

    def test_version_is_saved_with_the_list(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.enc")
            s = SubscriberStore.load(path, KEY, 5)
            s.set_menu_version(3)
            s.save()
            self.assertEqual(SubscriberStore.load(path, KEY, 5).menu_version, 3)

    def test_a_failed_publish_is_retried_next_run(self):
        class Bot(FakeBot):
            def set_commands(self, items):
                raise RuntimeError("telegram down")
        store = SubscriberStore("x", KEY, 5)
        quiet(commands.handle_updates, Bot(), Audience(["100"], store))
        self.assertEqual(store.menu_version, 0)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._old = (cfg.STATE_FILE, cfg.SEND_DELAY_SECONDS)
        cfg.STATE_FILE = os.path.join(self.tmp.name, "state.json")
        cfg.SEND_DELAY_SECONDS = 0
        self.addCleanup(lambda: (setattr(cfg, "STATE_FILE", self._old[0]),
                                 setattr(cfg, "SEND_DELAY_SECONDS", self._old[1])))

    def upcoming(self, minutes, impact="High", country="USD", title="FOMC"):
        when = NOW + timedelta(minutes=minutes)
        event = {"title": title, "country": country, "impact": impact, "date": when.isoformat()}
        return [(alerts.event_key(event, when), event, when)]

    def run_process(self, up, aud, bot=None, state=None):
        bot = bot or FakeBot()
        state = {} if state is None else state
        quiet(alerts.process, up, NOW, bot, state, aud)
        return bot, state

    def test_each_person_gets_only_what_they_chose(self):
        store = SubscriberStore("x", KEY, 10)
        store.subs = {"1": {"level": "high", "cur": ["EUR"]}, "2": {"level": "low", "cur": None}}
        aud = Audience([], store)
        bot, _ = self.run_process(self.upcoming(180, impact="Medium", country="USD"), aud)
        self.assertEqual([c for c, _ in bot.sent], ["2"])      # 1 wants high EUR only
        bot, _ = self.run_process(self.upcoming(180, impact="High", country="EUR"), aud)
        self.assertEqual(sorted(c for c, _ in bot.sent), ["1", "2"])

    def test_no_recipients_means_nothing_is_marked_sent(self):
        aud = Audience([], SubscriberStore("x", KEY, 10))
        bot, state = self.run_process(self.upcoming(180), aud)
        self.assertEqual((bot.sent, state), ([], {}))

    def test_no_duplicates_on_the_next_run(self):
        store = SubscriberStore("x", KEY, 10)
        store.subs = {"1": {"level": "high", "cur": None}}
        aud, up = Audience([], store), self.upcoming(180)
        bot, state = self.run_process(up, aud)
        bot, state = self.run_process(up, aud, bot, state)
        self.assertEqual(len(bot.sent), 1)

    def test_late_subscriber_gets_the_one_hour_reminder(self):
        store = SubscriberStore("x", KEY, 10)
        store.subs = {"1": {"level": "high", "cur": None}}
        aud = Audience([], store)
        up = self.upcoming(180)
        bot, state = self.run_process(up, aud)
        store.subs["2"] = {"level": "high", "cur": None}
        bot2 = FakeBot()
        quiet(alerts.process, up, NOW + timedelta(minutes=120), bot2, state, aud)  # 1 hour to go
        self.assertEqual(sorted(c for c, _ in bot2.sent), ["1", "2"])

    def test_one_failure_does_not_stop_others_and_blocked_user_is_removed(self):
        class Err(RuntimeError):
            code = 403

        class Bot(FakeBot):
            def send(self, message, chat_id=None, reply_markup=None):
                if chat_id == "1":
                    raise Err("blocked")
                super().send(message, chat_id)
        store = SubscriberStore("x", KEY, 10)
        store.subs = {"1": {"level": "high", "cur": None}, "2": {"level": "high", "cur": None}}
        aud = Audience(["100"], store)
        bot, state = self.run_process(self.upcoming(180), aud, Bot())
        self.assertEqual(sorted(c for c, _ in bot.sent), ["100", "2"])
        self.assertIsNone(store.get("1"))
        self.assertIn(True, [v for s in state.values() for v in s.values()])

    def test_everyone_failing_is_retried_next_run(self):
        class Down(FakeBot):
            def send(self, *a, **k):
                raise RuntimeError("down")
        store = SubscriberStore("x", KEY, 10)
        store.subs = {"1": {"level": "high", "cur": None}}
        aud, up = Audience([], store), self.upcoming(180)
        _, state = self.run_process(up, aud, Down())
        self.assertFalse(any(any(s.values()) for s in state.values()))
        bot, _ = self.run_process(up, aud, None, state)
        self.assertEqual(len(bot.sent), 1)

    def test_without_an_audience_nothing_changes(self):
        bot = FakeBot()
        bot.send = lambda message, chat_id=None, reply_markup=None: bot.sent.append((chat_id, message))
        quiet(alerts.process, self.upcoming(180), NOW, bot, {})
        self.assertEqual([c for c, _ in bot.sent], [None])


class BuildAudienceTests(unittest.TestCase):
    def test_off_without_a_key(self):
        with mock.patch.object(cfg, "SUBSCRIBERS_KEY", ""):
            aud = quiet(subs.build_audience, ["100"])
        self.assertIsNone(aud.store)

    def test_wrong_key_falls_back_to_owner_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.enc")
            good = SubscriberStore(path, KEY, 5)
            good.update("1", level="high")
            good.save()
            with mock.patch.object(cfg, "SUBSCRIBERS_KEY", "another-long-random-secret-key-99"), \
                    mock.patch.object(cfg, "SUBSCRIBERS_FILE", path):
                aud = quiet(subs.build_audience, ["100"])
        self.assertIsNone(aud.store)


if __name__ == "__main__":
    unittest.main()
