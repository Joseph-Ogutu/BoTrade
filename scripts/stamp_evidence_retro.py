"""One-off: stamp evidence files captured before the probe scripts recorded time.

Capture time is reconstructed from the file's modification time (read with Python in UTC, not with `ls`), which is
accurate to seconds because each probe wrote its file immediately after the response arrived. Where the response
itself carries a server timestamp (proposal date_start, or the `time` reply in the batch file) it is recorded too and
serves as an independent cross-check of the local clock.
"""
import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from derivbot.common.evidence import stamp_file  # noqa: E402
from derivbot.common.timeutil import iso_from_epoch  # noqa: E402

for f in sorted((Path(__file__).resolve().parents[1] / "docs" / "evidence").glob("*.json")):
    data = json.loads(f.read_text())
    if "_evidence" in data:
        continue
    mtime = dt.datetime.fromtimestamp(os.stat(f).st_mtime, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    server = None
    if isinstance(data.get("proposal"), dict) and "date_start" in data["proposal"]:
        server = iso_from_epoch(data["proposal"]["date_start"])
    elif isinstance(data.get("responses"), dict) and "time" in data["responses"].get("time", {}):
        server = iso_from_epoch(data["responses"]["time"]["time"])
    request = data.get("echo_req")
    note = ("Machine local zone is Africa/Nairobi (UTC+3): the same instant is 3 hours later on the local clock. "
            "Capture time is the file modification time; the probe scripts did not record time at capture.")
    stamp_file(f, captured_at_utc=mtime, provenance="reconstructed from file modification time",
               request=request, server_time_utc=server, note=note)
    print(f"{f.name}: captured_at_utc={mtime} server_time_utc={server}")
