"""Second authenticated demo check: OTP -> demo WebSocket -> ping, balance, two proposal QUOTES. No buy, ever.

Everything printed is redacted. Full redacted replies go to data/private/ (git-ignored: account ids and balances).
Sends only: ping, balance (no subscribe), proposal (quote only). UNVERIFIED until run: OTP response, balance shape,
whether loginid is reported, the minimum-stake error text (docs/api-notes.md items 4, 6, 7).
"""
import asyncio
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from derivbot.common.redaction import redact_obj, register_secret  # noqa: E402
from derivbot.common.timeutil import utc_now_iso  # noqa: E402
from derivbot.config.settings import load_settings, parse_env_file  # noqa: E402
from derivbot.deriv.messages import req_balance, req_ping, req_proposal  # noqa: E402
from derivbot.guard.otp import HttpOtpProvider  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


async def main() -> int:
    env = {**parse_env_file(ROOT / ".env"), **os.environ}
    env.setdefault("DATABASE_URL", "postgresql+psycopg://unused:unused@127.0.0.1/unused")
    env.setdefault("REDIS_URL", "redis://127.0.0.1/0")
    env["DERIV_PAT_DEMO"] = (ROOT / "secrets" / "deriv_pat_demo").read_text().strip()
    settings = load_settings(env)
    register_secret(env["DERIV_PAT_DEMO"])
    url = await HttpOtpProvider.from_settings(settings).get_url()
    print("OTP: got a URL that classifies as the exact demo endpoint (value not shown)")
    replies: list[dict] = []
    async with websockets.connect(url, open_timeout=15) as ws:
        bal = req_balance(2, subscribe=False)
        sends = [req_ping(1), bal,
                 req_proposal("1HZ100V", "CALL", Decimal("1"), 5, "t", "USD", 3),
                 req_proposal("1HZ100V", "CALL", Decimal("0.01"), 5, "t", "USD", 4)]
        for m in sends:
            await ws.send(json.dumps(m))
        got = set()
        try:
            while got != {1, 2, 3, 4}:
                raw = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                replies.append(redact_obj(raw))
                if isinstance(raw.get("req_id"), int):
                    got.add(raw["req_id"])
        except TimeoutError:
            print("timeout waiting for replies; got req_ids:", sorted(got))
    out = json.dumps(replies, indent=2)
    private = ROOT / "data" / "private"
    private.mkdir(parents=True, exist_ok=True)
    (private / f"auth_demo_{utc_now_iso().replace(':', '')}.json").write_text(out)
    print(out[:6000])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
