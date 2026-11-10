"""Expiry reminders: one message the evening before and one the morning of,
only for the instruments each person picked. What was already sent is kept per
person inside the encrypted subscribers file (never in the public repo)."""
import time
from datetime import datetime, time as clock, timedelta

import config as cfg
import expiry

EVE_FROM = clock(17, 0)       # IST: the evening before
MORNING_FROM = clock(8, 30)   # IST: the morning of expiry...
MORNING_UNTIL = clock(15, 30)  # ...until the markets close


def _stage(days_left, ist_time):
    if days_left == 1 and ist_time >= EVE_FROM:
        return "eve"
    if days_left == 0 and MORNING_FROM <= ist_time <= MORNING_UNTIL:
        return "day"
    return None


def due_for(keys, now):
    """[(token, key, row, stage)] reminders that are due right now for these instruments."""
    ist = now.astimezone(expiry.IST)
    today = ist.date()
    out = []
    for key in keys:
        for row in expiry.upcoming(key, today, count=3):
            stage = _stage((row["date"] - today).days, ist.time())
            if stage:
                out.append((f"{key}|{row['date'].isoformat()}|{stage}", key, row, stage))
    return out


def _message(items, stage, today):
    head = "⏰ <b>Expiry tomorrow</b>" if stage == "eve" else "🚨 <b>Expiry today</b>"
    lines = [head, ""]
    for _, key, row, _ in items:
        mark = "~" if row["est"] else ""
        lines.append(f"{expiry.label(key)}: {row['kind']}, <b>{mark}{expiry._day_text(row['date'])}</b>")
    if any(expiry.INSTRUMENTS[k][3] == "MCX" for _, k, _, _ in items):
        lines += ["", f"<i>{expiry.MCX_NOTE}</i>"]
    if any(k in expiry.NSE_COMMODITY for _, k, _, _ in items):
        lines += ["", f"<i>{expiry.NSE_NOTE}</i>"]
    return "\n".join(lines)


def send_expiry_reminders(notifier, audience, now):
    """Send what is due. Returns how many messages went out."""
    store = audience.store
    if store is None:
        return 0
    today = expiry.today_ist(now)
    sent = 0
    for chat_id, keys in audience.expiry_picks().items():
        done = set((store.get(chat_id) or {}).get("xs", ()))
        due = [d for d in due_for(keys, now) if d[0] not in done]
        for stage in ("eve", "day"):
            items = [d for d in due if d[3] == stage]
            if not items:
                continue
            try:
                notifier.send(_message(items, stage, today), chat_id=chat_id)
            except Exception as e:
                print("EXPIRY REMINDER FAILED:", e)
                if getattr(e, "code", None) == 403:
                    audience.remove(chat_id)
                    break
                continue
            for token, *_ in items:
                store.mark_expiry_sent(chat_id, token)
            sent += 1
            time.sleep(cfg.SEND_DELAY_SECONDS)
    store.prune_expiry_sent((today - timedelta(days=7)).isoformat())
    return sent
