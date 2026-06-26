PYTHON := .venv/bin/python
PIP := .venv/bin/pip
RUFF := .venv/bin/ruff

.PHONY: init run start test lint format check

init:
	./init.sh

run:
	./start.sh

start: run

test:
	$(PYTHON) test_etf.py

lint:
	$(RUFF) check .

format:
	$(RUFF) format .

check:
	$(MAKE) lint
	$(MAKE) test
