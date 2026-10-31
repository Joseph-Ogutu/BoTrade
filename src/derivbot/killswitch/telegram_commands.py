"""Telegram command handling (HANDBOOK 11.1): an allowlist of chat ids and exactly three commands.

/status reports, /pause and /kill engage the kill switch. There is deliberately no release, no resume and no setting
change: this class is constructed with the kill switch and a read-only status callable only, so it cannot reach the
executor, the settings or the real-trading guard. Release is an admin API or host action with a logged reason.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from derivbot.killswitch.switch import KillSwitch


class TelegramCommands:
    def __init__(self, kill_switch: KillSwitch, allowed_chat_ids: set[int],
                 status_provider: Callable[[], dict[str, Any]],
                 on_unauthorized: Callable[[int, str], None] | None = None) -> None:
        self._ks = kill_switch
        self._allowed = frozenset(allowed_chat_ids)
        self._status = status_provider
        self._on_unauthorized = on_unauthorized

    def handle(self, chat_id: int, text: str) -> str | None:
        """Return the reply to send, or None to stay silent (unlisted chat)."""
        if chat_id not in self._allowed:
            if self._on_unauthorized:
                self._on_unauthorized(chat_id, text)
            return None
        parts = text.strip().split()
        command = parts[0].split("@", 1)[0].lower() if parts else ""
        if command == "/status":
            info = dict(self._status())
            state = "ENGAGED" if self._ks.is_engaged() else "CLEAR"
            lines = [f"Kill switch: {state}"] + [f"{k}: {v}" for k, v in sorted(info.items())]
            return "\n".join(lines)
        if command in ("/pause", "/kill"):
            self._ks.engage("telegram", command[1:])
            return "Kill switch ENGAGED. New trading is stopped. Open contracts keep being monitored. Release needs the admin API."
        return "Unknown command. Available: /status, /pause, /kill"
