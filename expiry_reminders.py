"""Expiry reminder: one message the evening before expiry (from 5 pm IST), only
for the instruments each person picked, weekly and monthly kept apart. What was
already sent is kept per person inside the encrypted subscribers file."""
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
        items = [d for d in due_for(keys, now) if d[0] not in done]
        if not items:
            continue
        try:
            notifier.send(_message(items), chat_id=chat_id)
        except Exception as e:
            print("EXPIRY REMINDER FAILED:", e)
            if is_dead_chat(e):
                audience.remove(chat_id)
            continue
        for token, *_ in items:
            store.mark_expiry_sent(chat_id, token)
        sent += 1
        time.sleep(cfg.SEND_DELAY_SECONDS)
    store.prune_expiry_sent((today - timedelta(days=7)).isoformat())
    return sent
