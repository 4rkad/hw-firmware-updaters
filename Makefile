PYTHON ?= python3
VERSION ?= 0.1.2

.PHONY: test lint screenshots build clean

test:
	$(PYTHON) -m pytest -q ledger/tests

lint:
	ruff check trezor ledger bitbox common docs/make_screenshots.py

screenshots:
	QT_QPA_PLATFORM=offscreen $(PYTHON) docs/make_screenshots.py trezor
	QT_QPA_PLATFORM=offscreen $(PYTHON) docs/make_screenshots.py ledger
	QT_QPA_PLATFORM=offscreen $(PYTHON) docs/make_screenshots.py bitbox

build:
	VERSION=$(VERSION) ./packaging/build_linux.sh

clean:
	rm -rf build
