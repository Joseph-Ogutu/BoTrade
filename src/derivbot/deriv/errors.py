"""Errors raised by the Deriv adapter."""

from __future__ import annotations

from typing import Any


class DerivApiError(Exception):
    """The server answered with an `error` object. Never retried blindly."""

    def __init__(self, code: str | None, message: str | None, req_id: int | None = None,
                 msg_type: str | None = None) -> None:
        super().__init__(f"Deriv API error code={code} msg_type={msg_type} req_id={req_id}: {message}")
        self.code = code
        self.message = message
        self.req_id = req_id
        self.msg_type = msg_type

    @classmethod
    def from_message(cls, raw: dict[str, Any]) -> "DerivApiError":
        # OBSERVED-ERR: shape {"error": {"code", "message", "details"?}, "msg_type", "req_id", "echo_req"} seen live on
        #   the PUBLIC endpoint 2026-10-31 (UTC) for proposal, ticks, balance and portfolio failures.
        # UNVERIFIED-ERR: the same shape on buy, sell, proposal_open_contract and any authenticated (demo) path.
        err = raw.get("error") or {}
        if not isinstance(err, dict):
            err = {"message": str(err)}
        return cls(err.get("code"), err.get("message"), raw.get("req_id"), raw.get("msg_type"))
