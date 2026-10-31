"""Telegram command poller: long-polls the transport, hands each message to TelegramCommands (allowlist plus the three
commands) and sends back the reply. UNVERIFIED against the real Telegram API; tested with FakeTransport only."""

from __future__ import annotations

import asyncio
import logging

from derivbot.killswitch.telegram_commands import TelegramCommands
from derivbot.notify.transport import Transport, TransportError

log = logging.getLogger(__name__)


class TelegramPoller:
    def __init__(self, transport: Transport, commands: TelegramCommands, poll_timeout_s: float = 25.0,
                 error_backoff_s: float = 5.0) -> None:
        self._t, self._cmds, self._timeout, self._backoff = transport, commands, poll_timeout_s, error_backoff_s
        self._offset: int | None = None

    async def poll_once(self) -> int:
        updates = await self._t.get_updates(self._offset, self._timeout)
        handled = 0
        for u in updates:
            self._offset = max(self._offset or 0, int(u["update_id"]) + 1)
            msg = u.get("message") or {}
            chat, text = (msg.get("chat") or {}).get("id"), msg.get("text")
            if not isinstance(chat, int) or not isinstance(text, str):
                continue
            reply = self._cmds.handle(chat, text)
            handled += 1
            if reply is not None:
                try:
                    await self._t.send(chat, reply)
                except TransportError:
                    log.warning("could not send a telegram reply")
        return handled

    async def run(self, halt: asyncio.Event) -> None:
        while not halt.is_set():
            try:
                await self.poll_once()
            except TransportError:
                try:
                    await asyncio.wait_for(halt.wait(), timeout=self._backoff)
                except asyncio.TimeoutError:
                    pass
            except Exception:  # noqa: BLE001
                log.exception("telegram poller error")
                await asyncio.sleep(self._backoff)
