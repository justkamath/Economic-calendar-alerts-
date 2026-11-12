"""Notification channels. Each exposes send(message, chat_id=None)."""
from notifiers.telegram import TelegramError, TelegramNotifier

__all__ = ["TelegramError", "TelegramNotifier"]
