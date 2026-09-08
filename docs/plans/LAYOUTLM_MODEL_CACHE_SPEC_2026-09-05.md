# LayoutLM model distribution: per-env, versioned, pointer-driven

Build spec, 2026-09-05. Author: Fable (Claude). Builder: Astra. Reviewer: Fable.

## The problem this fixes (read this first — it is why every requirement below exists)

The Mac OCR drains for **dev and prod share one LayoutLM model cache per machine**,
and that cache never refreshes. Three independent faults combine:

1. **Env-agnostic cache path.** `Config.swift` defaults `layoutLMLocalCachePath`
   to `.models/layoutlm`, relative to cwd. `scripts/ocr_runner/run-dev.sh` and
   `run-prod.sh` both `cd "$SWIFT_DIR"` (the same checkout) and neither passes
   `--layoutlm-cache-path`. Two environments resolve to one directory.
2. **"Cached" means "exists," not "matches."** `ModelDownloader.isModelCached`
   only checks that `vocab.txt`, `config.json`, and some `.mlpackage` exist.
   No ETag, hash, or version is compared. A new export never reaches a worker
   unless a human deletes the directory on every machine. Prod's bucket key
   `coreml/layoutlm-coreml-bundle.zip` did not exist until 2026-09-05 and no
   one noticed, because workers never asked S3 — they served dev's cache.
3. **Latent stale-compile bug.** `LayoutLMInference` compiles the `.mlpackage`
   to `LayoutLM.mlmodelc` and persists it *inside the same cache directory*,
   reusing it whenever it exists. `extractZip` runs `unzip -o` into the existing
   directory without clearing it. A naive fix for (2) — "re-download when S3
   changed" — would land the new `.mlpackage` next to the **old** `.mlmodelc`,
   and the worker would load the old compiled model while logging success.

Underneath: the canonical key `coreml/layoutlm-coreml-bundle.zip` is a mutable
pointer with no version in its name, overwritten by every export. Version
identity already exists (`model_identity.json` inside the zip: `export_id`,
`training_job_id`, `quantize`, `exported_at`; and `coreml_canonical_bundle_etag`
on the DynamoDB Job) but nothing on the read side consumes it.

## Design

### S3 layout, per environment bucket (`layoutlm-training-{dev,prod}-68164770`)

```
coreml/versions/<export_id>/layoutlm-coreml-bundle.zip   immutable; written once by the exporter
coreml/active.json                                        pointer; the ONLY thing promotion writes
coreml/layoutlm-coreml-bundle.zip                         legacy alias; kept in this PR, removed later
```

`active.json` schema (all fields required):

```json
{
  "schema_version": 1,
  "export_id": "846035d1-1fcd-4008-913c-03c7af9deea7",
  "training_job_id": "177f4570-84ad-4fbf-aca2-c5c0bf6852bb",
  "training_job_name": "layoutlm-v32-random-20260904c",
  "bundle_key": "coreml/versions/846035d1-1fcd-4008-913c-03c7af9deea7/layoutlm-coreml-bundle.zip",
  "bundle_etag": "\"b0f3e8c5d6e5534cfc7e94f818add5b6-50\"",
  "bundle_size_bytes": 414952022,
  "promoted_at": "2026-09-05T17:35:08+00:00",
  "promoted_by": "set_active_model"
}
```

### Worker read path (Swift, `ModelDownloader`)

At each drain start, `ensureModelDownloaded(bucket:key:localCachePath:)` gains
a pointer-aware sibling (keep the old signature working for callers/tests):

1. `GET <bucket>/coreml/active.json`. If it exists and parses, the target is
   `(export_id, bundle_key)` from the pointer. If it does not exist, fall back
   to the legacy alias key **and** derive a version id from the alias object's
   ETag via `HEAD` (strip quotes; multipart ETags are fine as opaque ids). If
   neither exists, throw `ModelDownloaderError.noActiveModel(env)` — the drain
   must log `layoutlm_no_active_model` and continue **without** LayoutLM. It
   must never fall back to a directory it did not download for this env.
2. The cache is **versioned**: `<cachePath>/<version_id>/`. If that directory
   exists and validates, use it — no download.
3. Otherwise download the bundle to `<cachePath>/.tmp-<version_id>-<random>/`,
   extract there, validate, then atomically `rename` the temp dir to
   `<cachePath>/<version_id>/`. A crash mid-download leaves nothing the
   validator will accept. Remove any stale `.tmp-*` directories on start.
4. Validation = existing checks (`vocab.txt`, `config.json`, an `.mlpackage`)
   **plus** `model_identity.json` is present and, when the target came from a
   pointer, its `export_id` equals the pointer's `export_id`. Mismatch → throw
   `ModelDownloaderError.identityMismatch(expected:, found:)`; do not use it.
5. Prune: keep the current version plus the most recent one other version per
   cache path; delete older `<version_id>/` directories. Never delete the
   directory currently in use.
6. Log exactly one line per drain start:
   `layoutlm_model_active env=<env> version=<version_id> export_id=<…|none> training_job=<…|none> source=<pointer|alias> cached=<true|false> path=<dir>`.

The compiled `LayoutLM.mlmodelc` therefore lives inside `<version_id>/` next to
its own `.mlpackage` and can never be stale for a different model. Confirmed:
`LayoutLMInference` derives the compiled name from the `.mlpackage` filename,
not the directory, so versioned directories need no change there.

### Config (Swift)

`Config.load` defaults `layoutLMLocalCachePath` to `.models/layoutlm/<env>` when
`--env` is given and no explicit path is passed; `.models/layoutlm/local`
otherwise. An explicit `--layoutlm-cache-path` still wins. `Config` gains
`layoutLMPointerKey` defaulting to `coreml/active.json`, overridable from the
Pulumi output `layoutlm_model_pointer_key` or CLI `--layoutlm-pointer-key`.

### Runner scripts (`scripts/ocr_runner/run-dev.sh`, `run-prod.sh`)

Pass `--layoutlm-cache-path "$SWIFT_DIR/.models/layoutlm/<env>"` explicitly
(belt and braces over the Config default). Update `RUNNERS.md` to describe the
versioned cache and the pointer. Do **not** run `scripts/update_ocr_workers.sh`
or touch launchd — deployment is the reviewer's step.

### S3 client (Swift)

`S3ClientProtocol` gains `headObject(bucket:key:) async throws -> S3ObjectHead?`
(`eTag`, `contentLength`; `nil` on 404) and `getObjectIfExists(bucket:key:)
async throws -> Data?`. Implement in `SotoS3Client` with Soto's
`HeadObjectRequest`/`GetObjectRequest`; map NoSuchKey/404 to `nil`.

### Exporter (Python, `receipt_layoutlm/export_worker.py`)

In `process_export_job`, after building the bundle zip, also upload it to
`coreml/versions/<export_id>/layoutlm-coreml-bundle.zip` in the same bucket
derived from `output_s3_prefix`. Keep uploading the legacy alias. Add
`versioned_bundle_s3_uri` and `versioned_bundle_etag` to the result dict and to
`stamp_model_identity_on_job` (as `coreml_versioned_bundle_s3_uri` /
`coreml_versioned_bundle_etag`). Do **not** write `active.json` from the
exporter — export is not promotion.

### Promotion (Python, `scripts/receipt_mcp_server.py` `set_active_model_impl`)

Promotion is the single verb that makes the DynamoDB `active_model` tag and the
S3 pointer agree. After the existing tag flip, resolve the env's training bucket
(`layoutlm_training_bucket` from the Pulumi outputs the server already loads via
`PORTFOLIO_ENV`; add `training_bucket: str | None = None` to the impl signature
so tests inject it), read the Job's `coreml_versioned_bundle_s3_uri` (or fall
back to `coreml_export_id` → `coreml/versions/<export_id>/…`), `HEAD` it to get
etag/size, and `PutObject coreml/active.json`. If the Job has no CoreML identity,
return `{"success": false, "error": "job has no exported CoreML bundle; export before promoting"}`
**without** flipping the tag. Write the pointer with
`Content-Type: application/json` and `Cache-Control: no-cache`.

Add a small `scripts/promote_layoutlm_model.py --env {dev,prod} --job-name …
[--dry-run]` that (a) copies the immutable version zip cross-bucket when the
target env lacks it, (b) copies the Job entity into the target env's table if
absent, (c) calls the promotion impl. Dry-run prints every write it would make.
It must refuse `--env prod` unless `--yes-prod` is also passed.

### Pulumi (`infra/__main__.py`)

Export `layoutlm_model_pointer_key = "coreml/active.json"` for both stacks.
Keep `layoutlm_model_s3_key`. **Do not deploy anything.**

## Non-goals for this PR

- Removing the legacy alias upload or the alias fallback in the worker.
- Deploying to either Mac, kickstarting launchd, running `pulumi up`, or
  writing any `active.json` to a real bucket. The reviewer does the live steps.
- Changing training, labelling, or the label writer.

## Environment notes for the builder (read before running anything)

- This run executes inside the Codex `workspace-write` sandbox. SwiftPM's own
  `sandbox-exec` cannot nest inside it and fails with `Invalid manifest /
  sandbox_apply: Operation not permitted`. **Always pass `--disable-sandbox`
  to `swift build` and `swift test`.** The dependency cache under
  `receipt_ocr_swift/.build` was pre-built for you, so builds are incremental.
- `.venv/` at the repo root was pre-created with every editable package plus
  `pytest`, `moto`, `black==26.5.1`, `isort==8.0.1`. Use `.venv/bin/pytest`,
  `.venv/bin/black`, `.venv/bin/isort`. Do not recreate it; `pip install` of
  path-dependency packages by name (e.g. `receipt-places`) will not resolve.
- Network is enabled for `git push` and `gh`. Nothing else needs it.

## Acceptance — what "done" means

Tests, all passing locally (Swift: `swift build --configuration release
--disable-sandbox` and `swift test --disable-sandbox --filter
ModelDownloaderTests,ConfigTests`; Python: `.venv/bin/pytest
receipt_layoutlm/tests/unit -k "export_worker or promote"` and the MCP server
tests if any exist):

1. `ModelDownloaderTests` with a fake `S3ClientProtocol`:
   - pointer present → downloads `bundle_key`, extracts into `<version_id>/`,
     validates identity, logs `source=pointer cached=false`; second call hits
     the cache, logs `cached=true`, performs no `getObject`.
   - pointer absent, alias present → uses alias ETag as version; a second call
     with a **changed** ETag downloads again into a new directory and keeps the
     previous one (design step 5 retains current plus one); a third distinct
     ETag prunes the oldest, after the new version is in place.
     (Corrected during review: the original wording demanded pruning on the
     second version, contradicting step 5. The implementation follows step 5.)
   - pointer `export_id` ≠ bundle's `model_identity.json` → throws
     `identityMismatch`, and the temp directory is removed.
   - neither pointer nor alias → throws `noActiveModel`; a pre-existing
     directory from another version is **not** returned.
   - a leftover `.tmp-*` directory is removed on start.
2. `ConfigTests`: `--env dev` → `.models/layoutlm/dev`; `--env prod` →
   `.models/layoutlm/prod`; explicit path wins; no env → `.models/layoutlm/local`.
3. Python: exporter uploads the versioned key and stamps both new Job fields;
   `set_active_model_impl` writes `active.json` with every schema field, refuses
   a Job without CoreML identity without flipping the tag, and the promote
   script's dry run lists the cross-bucket copy, the Job copy, and the pointer
   write for a prod target only when `--yes-prod` is present.
4. `black --check --line-length=79` and `isort --check-only --profile=black
   --line-length=79` pass on every touched Python file. Swift builds with no
   new warnings.
5. Every log line named above appears verbatim in the code.

## Delivery

Work on branch `feat/layoutlm-model-cache` (already checked out). Commit in
logical units (Swift downloader, Swift config+runner, exporter, promotion,
Pulumi+docs). Include this file at `docs/plans/LAYOUTLM_MODEL_CACHE_SPEC_2026-09-05.md`.
Push the branch and open a **draft** PR against `main` titled
`feat(layoutlm): per-env versioned model cache with promotion pointer`. The PR
body must list which acceptance items pass, which do not, and anything in this
spec that turned out to be wrong about the code — say so plainly rather than
working around it. Do not merge. Do not touch `main`, prod, or any live AWS
resource. Unit tests must run offline (`moto`/fakes); do not call real S3.
