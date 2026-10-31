"""Guard: every error-code literal in code and tests is tagged OBSERVED-ERR or UNVERIFIED-ERR, and listed in STATE.md.

Why: the new Deriv API's error codes and shapes are mostly unknown (docs/api-notes.md). An untagged literal reads
like a fact. This test fails when a new code string appears without a tag within the 3 lines above it (or on the
same line), or when a tagged code is missing from the STATE.md list.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODE_LIT = re.compile(r'(?:"code"\s*:\s*|\bcode\s*=\s*|\.code\s*==\s*|\["code"\]\s*==\s*)"([A-Za-z]+)"')
TAG = re.compile(r"(?:UN)?VERIFIED-ERR|OBSERVED-ERR")
THIS = Path(__file__).resolve()


def py_files():
    for base in ("src", "tests"):
        for p in (ROOT / base).rglob("*.py"):
            if p.resolve() != THIS and "__pycache__" not in p.parts:
                yield p


def test_every_error_code_literal_is_tagged():
    untagged, codes = [], set()
    for p in py_files():
        lines = p.read_text().splitlines()
        for i, line in enumerate(lines):
            for m in CODE_LIT.finditer(line):
                window = lines[max(0, i - 3): i + 1]
                if any(TAG.search(w) for w in window):
                    codes.add(m.group(1))
                else:
                    untagged.append(f"{p.relative_to(ROOT)}:{i + 1}: {m.group(1)}")
    assert not untagged, "untagged error codes:\n" + "\n".join(untagged)
    assert codes, "expected to find tagged codes (regex out of date?)"


def test_every_tagged_code_is_listed_in_state_md():
    state = (ROOT / "STATE.md").read_text()
    missing = []
    for p in py_files():
        text = p.read_text()
        for m in CODE_LIT.finditer(text):
            if m.group(1) not in state:
                missing.append(f"{p.relative_to(ROOT)}: {m.group(1)}")
    assert not missing, "codes missing from STATE.md list:\n" + "\n".join(sorted(set(missing)))


def test_fake_codes_are_never_marked_observed():
    # An invented Fake* code must not carry an OBSERVED tag.
    for p in py_files():
        lines = p.read_text().splitlines()
        for i, line in enumerate(lines):
            m = CODE_LIT.search(line)
            if m and m.group(1).startswith("Fake"):
                window = " ".join(lines[max(0, i - 3): i + 1])
                assert "UNVERIFIED-ERR" in window and "OBSERVED-ERR" not in window, f"{p.name}:{i + 1}"
