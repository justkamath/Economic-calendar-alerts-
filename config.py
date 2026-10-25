"""
All settings live here. main.py contains logic only.

Secrets (never commit these):
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  - Local:   put them in a .env file (git-ignored)
  - Prod:    put them in .env.production (git-ignored) or, on GitHub
             Actions, in repo Settings -> Secrets and variables -> Actions

Real environment variables always win over values in .env files.
"""
import os
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent


def _load_env_file(path):
    """Minimal .env loader (no extra dependency). Does not override real env vars."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# Production file first so its values take precedence over .env
if os.environ.get("APP_ENV", "").lower() == "production":
    _load_env_file(BASE_DIR / ".env.production")
_load_env_file(BASE_DIR / ".env")


# ---------- Data source ----------
# One or more feeds, comma-separated. Each feed covers one calendar week.
API_URLS = tuple(
    u.strip() for u in os.environ.get(
        "CALENDAR_API_URLS",
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json,"
        "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
    ).split(",") if u.strip()
)

# ---------- Telegram ----------
TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_TIMEOUT = 20
SEND_DELAY_SECONDS = 0.5

# ---------- Secrets (from environment only) ----------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ---------- Runtime flags ----------
RESEND_DETECTION = os.environ.get("RESEND_DETECTION", "false").lower() == "true"

# ---------- Time ----------
UTC = timezone.utc
DISPLAY_TZ = ZoneInfo(os.environ.get("DISPLAY_TIMEZONE", "Asia/Kolkata"))
DISPLAY_TZ_LABEL = "IST"
TIME_FORMAT = "%d-%m-%Y %I:%M %p"

# ---------- Impact levels (most important first) ----------
# Rank decides sort order and the minimum-level filter.
IMPACT_RANK = {"high": 3, "medium": 2, "low": 1, "holiday": 0}
IMPACT_STYLE = {  # emoji, label shown in the alert heading
    "high": ("🔴", "HIGH"),
    "medium": ("🟠", "MEDIUM"),
    "low": ("🟡", "LOW"),
    "holiday": ("⚪", "HOLIDAY"),
}
# Lowest level to alert on: high | medium | low | holiday ("low" = all events
# except holidays, "holiday" = literally everything). Default keeps old behaviour.
_min = os.environ.get("ALERT_MIN_IMPACT", "high").strip().lower()
ALERT_MIN_IMPACT = _min if _min in IMPACT_RANK else "high"

# ---------- Alert logic ----------
HORIZON_DAYS = 31  # the free weekly feed only covers the current week
# Minutes-before-event windows (wide so late cron runs still catch them)
ONE_HOUR_WINDOW = (35, 65)    # exclusive low, inclusive high
THIRTY_MIN_WINDOW = (0, 35)

# ---------- State ----------
STATE_FILE = str(BASE_DIR / "sent_alerts.json")
KEEP_DAYS = 7
