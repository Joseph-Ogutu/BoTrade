#!/usr/bin/env python3
"""Set or change the dashboard password. Run it on the server in your own terminal; the password is typed hidden, never
passed on a command line, never written anywhere in clear. It writes only a salted scrypt hash to
secrets/dashboard_password_hash. No restart is needed: the bot reads that file at each login.

    python3 scripts/set_dashboard_password.py
"""
import getpass
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from derivbot.api.passwords import hash_password, validate_new_password  # noqa: E402

TARGET = ROOT / "secrets" / "dashboard_password_hash"


def main() -> int:
    pw = getpass.getpass("New dashboard password: ")
    problem = validate_new_password(pw)
    if problem:
        print(f"Not accepted: {problem}")
        return 1
    if getpass.getpass("Repeat it: ") != pw:
        print("The two entries differ. Nothing was changed.")
        return 1
    TARGET.parent.mkdir(mode=0o700, exist_ok=True)
    # Write INTO the existing file, never replace it: a container sees a mounted file by its inode, so a replaced file
    # would leave the running bot reading the old (empty) one until it is restarted.
    with open(TARGET, "w") as f:
        f.write(hash_password(pw) + "\n")
    os.chmod(TARGET, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)    # the container's non-root user must read it
    print(f"Saved a salted hash to {TARGET.relative_to(ROOT)}. Existing logins are signed out. You can log in now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
