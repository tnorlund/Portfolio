SHELL := /bin/bash

.PHONY: build test
build:
	cd receipt_ocr_swift && swift build --product receipt-ocr

test:
	cd receipt_ocr_swift && swift test

.PHONY: localstack-up localstack-down localstack-bootstrap e2e-stub
localstack-up:
	cd receipt_ocr_swift/LocalStack && docker compose up -d

localstack-down:
	cd receipt_ocr_swift/LocalStack && docker compose down -v

localstack-bootstrap:
	bash receipt_ocr_swift/LocalStack/bootstrap.sh

e2e-stub:
	E2E_LOCALSTACK=1 make -s test
