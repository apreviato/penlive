.PHONY: help usb frontend backend-deps test test-builder test-backend live image clean catalog-check catalog-update

REPO_ROOT := $(shell pwd)
LIVE_OUT  := $(REPO_ROOT)/live/build/out
DIST      := $(REPO_ROOT)/dist

help:
	@echo "BootStack targets:"
	@echo "  make usb             guided end-to-end USB build  [Linux + root]  <- start here"
	@echo ""
	@echo "  make frontend        build the React kiosk UI (required before 'make live')"
	@echo "  make test            run builder + backend test suites"
	@echo "  make live            build the Debian Live rootfs   [Linux + root + live-build]"
	@echo "  make image           build a flashable .img.zst      [Linux + root]"
	@echo "  make catalog-check   check catalog.json against vendor checksums"
	@echo "  make catalog-update  rewrite catalog.json from vendor checksums"
	@echo "  make dev-api         run the API locally in dev mode"
	@echo "  make dev-ui          run the Vite dev server"

# Wraps every other step: dependencies, frontend, live system, device choice
# and the write itself, asking before anything irreversible.
usb:
	sudo ./scripts/make-usb.sh

frontend:
	cd manager/frontend && npm ci && npm run build

backend-deps:
	python -m pip install -r manager/backend/requirements-dev.txt

test: test-builder test-backend

test-builder:
	cd builder && python -m pytest -q

test-backend:
	cd manager/backend && python -m pytest -q

catalog-check:
	python tools/update_catalog.py

catalog-update:
	python tools/update_catalog.py --write

live: frontend
	sudo ./live/build.sh

image:
	sudo python -m bootstack.cli image $(DIST)/bootstack-amd64.img \
		--live-dir $(LIVE_OUT) \
		--grub-cfg grub/grub.cfg \
		--recovery-cfg grub/recovery.cfg \
		--catalog catalog/catalog.json \
		--compress

dev-api:
	cd manager/backend && BOOTSTACK_DEV=1 python -m uvicorn app.main:app --reload --port 7777

dev-ui:
	cd manager/frontend && npm run dev

clean:
	rm -rf live/build dist manager/frontend/dist devdata make-usb.log
