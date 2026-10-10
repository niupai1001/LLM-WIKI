PYTHON := .venv/bin/python
LIMIT ?= 3
MINUTES ?= 30
CATEGORY ?=

.PHONY: setup test web collector ingest stats drafts prepare approve

setup:
	@test -f .env || cp .env.example .env
	uv venv --python 3.12 .venv
	uv pip sync --python $(PYTHON) requirements.lock.txt

test:
	$(PYTHON) -m unittest discover -s tests -v

web:
	$(PYTHON) -m uvicorn station.web:app --host 127.0.0.1 --port 8765

collector:
	$(PYTHON) -u -m station.cli loop --minutes $(MINUTES)

ingest:
	$(PYTHON) -u -m station.cli ingest

stats:
	$(PYTHON) -m station.cli stats

drafts:
	$(PYTHON) -m station.cli drafts

prepare:
	$(PYTHON) -m station.cli prepare --limit $(LIMIT) --category "$(CATEGORY)"

approve:
	@test -n "$(ID)" || (echo 'Usage: make approve ID=<source-id>'; exit 1)
	$(PYTHON) -m station.cli approve "$(ID)"
