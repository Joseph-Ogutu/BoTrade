"""Message transports. FakeTransport is for tests. TelegramHttpTransport is UNVERIFIED: it has never run, because no bot
token exists yet. The Telegram bot token travels inside the request URL path, so every exception text from the HTTP
client is redacted before it can reach a log or an alert."""

from __future__ import annotations

from typing import Any, Protocol

from derivbot.common.redaction import redact_text, register_secret


class TransportError(Exception):
    pass


class Transport(Protocol):
    async def send(self, chat_id: int, text: str) -> None: ...
    async def get_updates(self, offset: int | None, timeout_s: float) -> list[dict[str, Any]]: ...


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.incoming: list[dict[str, Any]] = []
        self.fail_next = 0
        self.offsets: list[int | None] = []

    async def send(self, chat_id: int, text: str) -> None:
        if self.fail_next:
            self.fail_next -= 1
            raise TransportError("fake transport failure")
        self.sent.append((chat_id, text))

    async def get_updates(self, offset: int | None, timeout_s: float) -> list[dict[str, Any]]:
        self.offsets.append(offset)
        out, self.incoming = self.incoming, []
        return [u for u in out if offset is None or u["update_id"] >= offset]

    def push_message(self, update_id: int, chat_id: int, text: str) -> None:
        self.incoming.append({"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": text}})


class TelegramHttpTransport:  # pragma: no cover - needs a real bot token; UNVERIFIED
    def __init__(self, token: str, client: Any = None) -> None:
        import httpx

        register_secret(token)
        self._base = f"https://api.telegram.org/bot{token}"
        self._client = client or httpx.AsyncClient(timeout=40.0)

    async def send(self, chat_id: int, text: str) -> None:
        try:
            r = await self._client.post(f"{self._base}/sendMessage", json={"chat_id": chat_id, "text": text})
            r.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - the exception text contains the URL, and the URL contains the token
            raise TransportError(redact_text(f"{type(exc).__name__}: {exc}")) from None

    async def get_updates(self, offset: int | None, timeout_s: float) -> list[dict[str, Any]]:
        try:
            params: dict[str, Any] = {"timeout": int(timeout_s)}
            if offset is not None:
                params["offset"] = offset
            r = await self._client.get(f"{self._base}/getUpdates", params=params)
            r.raise_for_status()
            return list(r.json().get("result", []))
        except Exception as exc:  # noqa: BLE001
            raise TransportError(redact_text(f"{type(exc).__name__}: {exc}")) from None
