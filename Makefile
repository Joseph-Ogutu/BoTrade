.PHONY: test lint typecheck cov audit scan check power-table dev-up dev-down compose-check backup-pg

PY := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python)

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check .

typecheck:
	$(PY) -m mypy

cov:
	PY=$(PY) scripts/check_risk_coverage.sh

audit:
	$(PY) -m pip_audit -r requirements-bot.txt -r requirements-collector.txt -r requirements-dev.txt --no-deps --disable-pip

scan:
	$(PY) scripts/scan_history.py

# Everything CI runs, in the same order.
check: lint typecheck cov audit scan compose-check

compose-check:
	bash scripts/init_secrets.sh >/dev/null
	docker compose config -q

dev-up:
	scripts/dev_up.sh

dev-down:
	scripts/dev_down.sh

backup-pg:
	scripts/pg_backup.sh

power-table:
	$(PY) scripts/power_table.py > docs/power-table.md
