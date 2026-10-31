.PHONY: test power-table

PY := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python)

test:
	$(PY) -m pytest

power-table:
	$(PY) scripts/power_table.py > docs/power-table.md
