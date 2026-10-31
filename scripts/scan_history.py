"""Scan the whole git history (all refs) for secrets, env files, token file names and personal data.

Prints ONLY pattern names, commit hashes and file paths. It never prints matched text.
    python scripts/scan_history.py
"""
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(os.environ.get("SCAN_REPO") or Path(__file__).resolve().parents[1])


def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and r.returncode not in (0, 1):
        raise RuntimeError(r.stderr)
    return r.stdout


CONTENT_PATTERNS = {
    "private-key-block": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "aws-access-key": r"AKIA[0-9A-Z]{16}",
    "github-token": r"(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}",
    "slack-token": r"xox[abprs]-[A-Za-z0-9-]{10,}",
    "anthropic-key": r"sk-ant-[A-Za-z0-9_-]{20,}",
    "openai-style-key": r"sk-[A-Za-z0-9]{32,}",
    "jwt": r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.",
    "pat-prefixed-token": r"\bpat_[A-Za-z0-9]{16,}",
    "bearer-token-literal": r"[Bb]earer [A-Za-z0-9._~+/=-]{24,}",
    "otp-query-literal": r"[?&]otp=[A-Za-z0-9]{8,}",
    "url-with-credentials": r"[a-z]+://[^/\s:@]+:[^/\s@]+@[^/\s]+",
    "long-literal-assigned-to-secret-name": r"(?i)(token|secret|password|passwd|api[_-]?key|pat)[\"']?\s*[:=]\s*[\"'][A-Za-z0-9_./+=-]{24,}[\"']",
    "email-address": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}",
    "absolute-home-path": r"/home/[a-z0-9_-]+/",
}
NAME_PATTERNS = {
    "env-file": r"(^|/)\.env(\.|$)",
    "token-or-secret-name": r"(?i)(token|secret|credential|passwd|password|\.pem$|\.key$|id_rsa|\.p12$|\bpat\b)",
    "database-or-data-file": r"(?i)\.(db|sqlite3?|sqlite-wal|sqlite-shm|csv|parquet|log)$",
    "legacy-or-old-bot-tree": r"(?i)(^|/)(legacy|old[-_ ]?bot|deriv_bot|Joseph Trading Bot)(/|$)",
    "agent-or-editor-state": r"(^|/)(\.claude|\.kilo|\.vscode|\.idea)(/|$)",
}


def main() -> int:
    revs = git("rev-list", "--all").split()
    short = {r: r[:7] for r in revs}
    print(f"refs: {git('for-each-ref', '--format=%(refname)').split()}")
    print(f"commits scanned: {len(revs)}; remotes: {git('remote', '-v').strip() or 'none'}")

    print("\n== file names that ever existed in history (matching risky name patterns)")
    first_seen: dict[str, str] = {}
    cur = None
    for line in git("log", "--all", "--reverse", "--name-only", "--format=@@%h").splitlines():
        if line.startswith("@@"):
            cur = line[2:]
        elif line.strip():
            first_seen.setdefault(line, cur)
    for label, rx in NAME_PATTERNS.items():
        hits = sorted(p for p in first_seen if re.search(rx, p))
        print(f"[{label}] {len(hits)} path(s)")
        for p in hits:
            print(f"   {p}   (first in {first_seen[p]})")

    print("\n== content patterns across every commit (pattern, path, first commit, number of commits containing it)")
    for label, rx in CONTENT_PATTERNS.items():
        found: dict[str, list[str]] = defaultdict(list)
        for rev in revs:
            out = git("grep", "-l", "-I", "-E", rx, rev, check=False)
            for line in out.splitlines():
                if ":" in line:
                    found[line.split(":", 1)[1]].append(short[rev])
        print(f"[{label}] {len(found)} path(s)")
        for path, hs in sorted(found.items()):
            print(f"   {path}   first {hs[-1]}  commits {len(hs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
