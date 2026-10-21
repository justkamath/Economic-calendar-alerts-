"""Notification channels. Each exposes send(message)."""
from notifiers.telegram import TelegramNotifier

__all__ = ["TelegramNotifier"]
