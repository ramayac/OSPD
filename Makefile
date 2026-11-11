.PHONY: all test scan plan dry-run batch-run apply undo status report check-dates fix-dates review-export review-html review-approve review-approve-exact clean help

PYTHON ?= python3
RUN ?=
CONFIRM ?=
LIMIT ?=
ROOT ?=
CONFIG ?=
DATA_DIR ?=
FORCE ?=

COMMON_ARGS := $(if $(ROOT),--root $(ROOT)) $(if $(CONFIG),--config $(CONFIG)) $(if $(DATA_DIR),--data-dir $(DATA_DIR)) $(if $(FORCE),--force-insync)

all: help

help:
	@echo "================================================================="
	@echo "   OSPD (Organize and Sorting Photo Dump) Management Console    "
	@echo "================================================================="
	@echo "Quickstart:"
	@echo "  1. Copy config.json.example to config.json (and edit your pictures_root)"
	@echo "  2. make scan          Scan and index library (use HASH=1 for SHA256)"
	@echo "  3. make plan          Generate move plan"
	@echo "  4. make dry-run       Validate 100% collision-free moves"
	@echo "  5. make apply CONFIRM=--run   Execute all moves"
	@echo ""
	@echo "Targets:"
	@echo "  make test             Run test suite (pytest)"
	@echo "  make scan             Scan and index files (use LIMIT=100 or HASH=1)"
	@echo "  make review-export    Export CSVs for folders, lowres, and duplicates"
	@echo "  make review-html      Generate side-by-side HTML contact sheet"
	@echo "  make review-approve   Bulk-approve duplicate groups"
	@echo "  make review-approve-exact Bulk-approve exact SHA256 duplicates only"
	@echo "  make check-dates      List discrepancies between filename (>=90%) and metadata"
	@echo "  make fix-dates        Restore file mtime from filename, needs CONFIRM=--run"
	@echo "  make plan             Generate operation move plan"
	@echo "  make dry-run          Simulate moves and test rollback"
	@echo "  make batch-run        Execute pilot batch (default 200 ops), needs CONFIRM=--run"
	@echo "  make apply            Execute ALL planned moves, needs CONFIRM=--run"
	@echo "  make undo RUN=runXXX  Rollback specified run ID"
	@echo "  make report           Generate Markdown and HTML execution reports"
	@echo "  make status           Display system status, files, and recent runs"
	@echo "  make clean            Clean test caches and temporary files"
	@echo ""
	@echo "Parameters:"
	@echo "  ROOT=/path/to/photos  Override pictures root directory"
	@echo "  CONFIG=custom.json    Path to custom configuration file"
	@echo "  DATA_DIR=/path/data   Directory for SQLite DB and reports"
	@echo "  HASH=1                Calculate SHA-256 for exact duplicates (scan)"
	@echo "  LIMIT=N               Limit operations to N items"
	@echo "  CONFIRM=--run         Confirmation flag required for disk modifications"
	@echo "================================================================="

test:
	$(PYTHON) -m pytest -v

scan:
	$(PYTHON) -m ospd.cli scan $(COMMON_ARGS) $(if $(LIMIT),--limit $(LIMIT)) $(if $(HASH),--calculate-hash)

plan:
	$(PYTHON) -m ospd.cli plan $(COMMON_ARGS)

dry-run:
	$(PYTHON) -m ospd.cli dry-run $(COMMON_ARGS) $(if $(LIMIT),--limit $(LIMIT))

batch-run:
	$(PYTHON) -m ospd.cli batch-run $(COMMON_ARGS) $(if $(LIMIT),--limit $(LIMIT),--limit 200) $(CONFIRM)

apply:
	$(PYTHON) -m ospd.cli apply $(COMMON_ARGS) $(CONFIRM)

undo:
	@if [ -z "$(RUN)" ]; then echo "Error: Specify run to undo with RUN=run00X"; exit 1; fi
	$(PYTHON) -m ospd.cli undo $(COMMON_ARGS) $(RUN)

review-export:
	$(PYTHON) -m ospd.cli review $(COMMON_ARGS) export

review-html:
	$(PYTHON) -m ospd.cli review $(COMMON_ARGS) html

review-approve:
	$(PYTHON) -m ospd.cli review $(COMMON_ARGS) approve-all

review-approve-exact:
	$(PYTHON) -m ospd.cli review $(COMMON_ARGS) approve-exact

check-dates:
	$(PYTHON) -m ospd.cli check-dates $(COMMON_ARGS)

fix-dates:
	$(PYTHON) -m ospd.cli fix-dates $(COMMON_ARGS) $(CONFIRM)

report:
	$(PYTHON) -m ospd.cli report $(COMMON_ARGS)

status:
	$(PYTHON) -m ospd.cli status $(COMMON_ARGS)

clean:
	rm -rf .pytest_cache tests/__pycache__ ospd/__pycache__
