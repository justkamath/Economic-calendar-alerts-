"""Expiry reminders, only for the instruments each person picked, weekly and
monthly kept apart: one the evening before expiry (from 5 pm IST) and one on the
day itself from the daily summary hour (8 am IST), sent right after the summary.
What was already sent is kept per person inside the encrypted subscribers file."""
import time
from datetime import time as clock, timedelta

import config as cfg
import expiry
from alerts import is_dead_chat

EVE_FROM = clock(17, 0)     # IST


def due_for(keys, now):
    """[(token, key, row)] instruments expiring tomorrow, once it is past 5 pm IST."""
    ist = now.astimezone(expiry.IST)
    if ist.time() < EVE_FROM:
        return []
    today = ist.date()
    out = []
    for key in keys:
        for row in expiry.upcoming(key, today, count=14):
            if (row["date"] - today).days == 1:
                out.append((f"{key}|{row['date'].isoformat()}|eve", key, row))
    return out


def due_today(keys, now):
    """[(token, key, row)] instruments expiring today, once the summary hour is reached."""
    ist = now.astimezone(expiry.IST)
    if ist.hour < cfg.SUMMARY_HOUR or ist.time() >= EVE_FROM:   # 8 am to 5 pm only
        return []
    today = ist.date()
    out = []
    for key in keys:
        for row in expiry.upcoming(key, today, count=14):
            if row["date"] == today:
                out.append((f"{key}|{row['date'].isoformat()}|am", key, row))
    return out


def _morning_message(items, today):
    parts = ["📅 <b>Expiry today</b>"]
    parts += expiry.render_sets([(k, r) for _, k, r in items], today)
    if any(r["est"] for _, _, r in items):
        parts.append(f"<i>{expiry.NOTE}</i>")
    return "\n\n".join(parts)


def _message(items):
    day = min(r["date"] for _, _, r in items)
    parts = [f"⏰ <b>Expiry tomorrow</b>\n<i>{expiry._day_text(day)}</i>"]
    parts += expiry.render_sets([(k, r) for _, k, r in items])
    if any(r["est"] for _, _, r in items):
        parts.append(f"<i>{expiry.NOTE}</i>")
    return "\n\n".join(parts)


def send_expiry_reminders(notifier, audience, now):
    """Send what is due. Returns how many messages went out."""
    store = audience.store
    if store is None:
        return 0
    today = expiry.today_ist(now)
    sent = 0
    for chat_id, keys in audience.expiry_picks().items():
        done = set((store.get(chat_id) or {}).get("xs", ()))
        morning = [d for d in due_today(keys, now) if d[0] not in done]
        evening = [d for d in due_for(keys, now) if d[0] not in done]
        for items, text in ((morning, _morning_message(morning, today) if morning else ""),
                            (evening, _message(evening) if evening else "")):
            if not items:
                continue
            try:
                notifier.send(text, chat_id=chat_id)
            except Exception as e:
                print("EXPIRY REMINDER FAILED:", e)
                if is_dead_chat(e):
                    audience.remove(chat_id)
                break
            for token, *_ in items:
                store.mark_expiry_sent(chat_id, token)
            sent += 1
            time.sleep(cfg.SEND_DELAY_SECONDS)
    store.prune_expiry_sent((today - timedelta(days=7)).isoformat())
    return sent
