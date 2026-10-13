PYTHON ?= .venv/bin/python
ifeq ($(wildcard $(PYTHON)),)
PYTHON := python3
endif

.PHONY: help test lint format

help:
	@echo "lint / format / test"

lint:
	$(PYTHON) -m ruff check src tests

format:
	$(PYTHON) -m ruff format src tests

test:
	$(PYTHON) -m pytest -q
