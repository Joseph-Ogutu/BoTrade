.PHONY: test power-table

test:
	python -m pytest -q

power-table:
	python scripts/power_table.py > docs/power-table.md
