#!/usr/bin/env bash
# Gate: 100% line AND branch coverage on the risk rules and the stake sizing (handbook item 4). Fails the build below that.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-$([ -x .venv/bin/python ] && echo .venv/bin/python || echo python)}"
rm -f .coverage
"$PY" -m coverage run -m pytest -o addopts="" -q tests
"$PY" -m coverage report --include="src/derivbot/risk/rules.py,src/derivbot/risk/stake.py" --show-missing --fail-under=100
"$PY" -m coverage report --fail-under="${TOTAL_MIN:-85}" > /dev/null
