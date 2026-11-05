"""First authenticated demo check: GET /trading/v1/options/accounts (read only, one request).

Prints the response SHAPE and non-secret values with secrets redacted. Writes the redacted response to data/private/
(git-ignored) because it holds account ids and balances. Never calls OTP, buy, reset or any write endpoint.
UNVERIFIED until this has run: the path, the headers and the response shape (docs/api-notes.md item 199).
"""
import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from derivbot.common.redaction import redact_obj, register_secret  # noqa: E402
from derivbot.common.timeutil import utc_now_iso  # noqa: E402
from derivbot.config.settings import parse_env_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://api.derivws.com"
PATH = "/trading/v1/options/accounts"   # the only path this script may call


def main() -> int:
    env = {**parse_env_file(ROOT / ".env"), **os.environ}
    app_id = env.get("DERIV_APP_ID_DEMO", "")
    pat = (ROOT / "secrets" / "deriv_pat_demo").read_text().strip()
    if not app_id or not pat:
        print("missing App ID or token")
        return 2
    register_secret(pat)
    r = httpx.get(BASE + PATH, headers={"Authorization": f"Bearer {pat}", "Deriv-App-ID": app_id, "Accept": "application/json"}, timeout=15)
    print("HTTP", r.status_code, "content-type:", r.headers.get("content-type"))
    try:
        body = redact_obj(r.json())
    except ValueError:
        print("non-JSON body, first 200 chars (redacted):", r.text[:200].replace(pat, "[REDACTED]"))
        return 1
    out = json.dumps(body, indent=2)
    private = ROOT / "data" / "private"
    private.mkdir(parents=True, exist_ok=True)
    (private / f"auth_accounts_{utc_now_iso().replace(':', '')}.json").write_text(out)
    print(out[:3000])
    return 0 if r.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
