# receipt_ocr_swift/ (Swift Mac worker)

Deltas to the root `AGENTS.md`. macOS only: Vision and CoreML are unavailable
on Linux agents, so build and run steps happen on the user's Mac.

- Build: `swift build --configuration release` (binary at
  `.build/arm64-apple-macosx/release/receipt-ocr`). Unit tests:
  `swift test --filter ReceiptOCRCoreTests`. Integration tests need LocalStack
  (`make localstack-up`, `make localstack-bootstrap`).
- Layout: `Sources/ReceiptOCRCLI/` (argument parsing, entry point) and
  `Sources/ReceiptOCRCore/` (`Config/`, `Worker/`, `OCR/`, `LayoutLM/`, `AWS/`).
  Tests mirror it under `Tests/`.
- Output must stay byte-compatible with the Python consumers: keep JSON field
  names and the timestamp format `yyyy-MM-dd'T'HH:mm:ss.SSSxxx` (`+00:00`).
  `XXXXX` emits `Z`, which Python's `datetime.fromisoformat` rejects.
- Configuration comes from Pulumi stack outputs via `--env dev`; agents never
  use `--env prod`. `--stub-ocr` exercises the queue flow without Vision.
- Operating the worker (queues, model cache, flags) is documented in the
  `mac-ocr-worker` skill; export of the CoreML model it loads is in
  `coreml-export`.
