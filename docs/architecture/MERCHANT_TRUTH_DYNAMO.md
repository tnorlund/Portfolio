# Merchant Truth in DynamoDB

Design for #1188 P4: move per-merchant "truth" (measured typography, stylemaps,
layout templates, asset pointers, engine flags, catalog snapshots) out of
`scripts/merchant_profiles.json` and scattered S3 JSONs into versioned,
content-hashed, provenance-carrying records in the existing single table,
using proper DynamoDB access patterns.

Status: DESIGN (docs-only PR). Implementation lands as #1188 P4 after review.

## 0. Goals and non-goals

One versioned bundle per merchant. Versions are **immutable** outputs of the
S0–S6 measurement pipeline; the only mutable record is a single ACTIVE pointer
whose flip is the reviewed "merge" step. Engine behavior flags are carried
alongside but sourced from git, not measurement (truth = measured; flags =
decided).

Non-goals:

- Storing binaries — npz glyph atlases, logo PNGs, eval sheets stay in S3
  behind `{s3_key, content_hash}` pointers (the `MerchantFont` precedent).
- Storing glyph stroke sources / glyph-studio skeletons — those stay in git.
- Replacing instance truth — `RECEIPT_SECTION`, per-line typography records,
  etc. are per-receipt measurements and stay as-is.

## 1. Access patterns first

Enumerated from the actual consumers (audited on main + the mini worktrees):

| Consumer | Access pattern | Served by |
|---|---|---|
| Renderer (`scripts/render_synthetic_receipts.py`) | one bundle read at render start (today: whole-registry in-process cache at first call, then dict lookups; font/stylemap/logo point-read only on local cache miss, sha-verified, never raises) | read ACTIVE → manifest → components, once per process, through the loader cache |
| Alias resolution (today a linear scan over profile `aliases`) | name → canonical merchant | `C#identity` alias map |
| Measurement writers (glyphstudio stylescan/styleagg/layout_template) | put new version with provenance; today `layout_template.py` writes into `merchant_profiles.json` with a git dirty-stamp guard as its only "versioning" | `mint_version`/`seal_version`; dirty-stamp guard becomes the conditional create |
| Eval (`full_fidelity_eval.py`) | get active layout template by merchant, schema-validated | ACTIVE → `C#layout` |
| Fleet status | list all merchants with truth + active summary — **does not exist as a tool today** (enumeration = profiles keys + `env.mjs FONT_MERCHANTS`); this design creates it | one GSITYPE query on `MERCHANT_TRUTH_ACTIVE` |
| Compose engine | list catalog by merchant (+category `begins_with`) — today re-derives the catalog per run | `MerchantCatalogItem` partition query (unchanged) |
| dev→prod mirror (`reconcile_dev_to_prod.py`) | copy truth to prod | new reconcile leg (§5 risk 1) |
| Agent sessions (owner observations) | propose / review / resolve | `PROPOSED#` records + `resolve_proposal`; TTL usable for expiring drafts |

Measured sizes (from the mini): `merchant_profiles.json` is 15.6KB total for
16 merchants, max profile 4KB (Costco; its layout_template block 2.5KB);
stylemaps 0.7–8.9KB with a 41KB outlier (Target). Everything fits single
items with ≥10× margin under 400KB; the S3-spillover path is a safety valve,
not the normal case.

## 2. Entity set and key design

Single table, existing GSIs only (GSI1..GSI4 + GSITYPE with hash=TYPE, per
`infra/dynamo_db.py`). Merchant slug via the existing `slugify_merchant()`
(`receipt_dynamo/entities/merchant_catalog_item.py`) — this also fixes the
current split-brain where `MerchantFont` keys on exact `merchant_name` while
catalogs key on slug; the identity component records the mapping.

**PK = `MERCHANT_TRUTH#{slug}`** for all records below.

| Record | SK | TYPE | Notes |
|---|---|---|---|
| Active pointer | `TRUTH#ACTIVE` | `MERCHANT_TRUTH_ACTIVE` | `{version, bundle_hash, activated_at, activated_by, gate_status, prev_version}` + denormalized fleet summary fields |
| Version manifest | `TRUTH#v{n:05d}#MANIFEST` | `MERCHANT_TRUTH_MANIFEST` | component list w/ per-component `content_hash`, `bundle_hash`, `status` (OPEN→SEALED), provenance block, gate results |
| Component | `TRUTH#v{n:05d}#C#{component}` | `MERCHANT_TRUTH_COMPONENT` | component ∈ identity, typography, stylemap, layout, assets, flags, catalog_snapshot |
| Proposal | `PROPOSED#{iso_ts}#{claim-slug}` | `MERCHANT_TRUTH_PROPOSAL` | owner observation, status OPEN/CONFIRMED/REFUTED |
| Audit | `AUDIT#{iso_ts}#{ulid}` | `MERCHANT_TRUTH_AUDIT` | one per mint/seal/flip/rollback |

Key-grammar payoffs (all plain Query, no GSI needed):

- Full bundle read: Query `begins_with(SK, "TRUTH#v00042#")`.
- Latest versions: Query `begins_with(SK, "TRUTH#v")` with
  `ScanIndexForward=False` — zero-padded version makes lexicographic =
  numeric; `ACTIVE` sorts before `v` so it never pollutes; within a version
  `C#` < `MANIFEST` so descending yields the manifest first.
- Everything about a merchant: Query PK.

GSI usage: fleet enumeration (`fleet_status`) = one GSITYPE query for
`TYPE = MERCHANT_TRUTH_ACTIVE` — the pointer denormalizes
`{slug, version, bundle_hash, gate_status, activated_at}` so fleet status
needs no fan-out. Listing OPEN proposals: GSITYPE on
`MERCHANT_TRUTH_PROPOSAL` (filter `status=OPEN`; volume is tiny). No new GSI
keys are required. An optional GSI1 mirror (`GSI1PK=MERCHANT_TRUTH`,
`GSI1SK=SLUG#{slug}#v{n}` on manifests, following `MerchantCatalogItem`'s
convention) is deferred — GSITYPE covers every known access pattern.

**400KB limit:** component-per-item, never a monolith. Measured components
today are small (largest `layout_template` block ≈ a few KB; stylemaps tens
of KB). The writer enforces: serialized payload > 300KB → store
`payload_s3_key` + `payload_size` instead of inline `payload`;
`content_hash` is always inline so gates and diffs never need S3.

**The item catalog does NOT inline.** It stays item-per-product as
`MerchantCatalogItem` (PK=`MERCHANT_CATALOG#{slug}`) — a proven shape with
hundreds of items, queryable by category. The bundle's `C#catalog_snapshot`
is a snapshot descriptor: `{item_count, catalog_hash (sha256 of sorted item
keys+prices), as_of}` — a version pins what the catalog looked like without
duplicating it.

**Content hashing:** per-component sha256 of canonical JSON (sorted keys,
compact separators); `bundle_hash` = sha256 of the sorted
`"{component}:{hash}"` lines. Asset entries hash the S3 object bytes
(already the `MerchantFont` convention: `content_hash` + `source_commit`).
Renders and evals record `{slug, version, bundle_hash}` into the recipe hash,
so the #1188 freshness gate ("recipe-hash asserted, stale sheets
structurally impossible") extends to merchant truth for free.

**Provenance (on the manifest, required):** `source_receipt_keys` (the
receipts measured), `pipeline` + `pipeline_version` (e.g.
`glyphstudio.layout_template@1`), `git_sha` + `dirty` flag
(`layout_template.py` already records `tool_git_sha`/`tool_dirty` — lift
as-is), `measured_at`, `written_by` (writer identity, §3), and
`confirmed_proposals` (proposal SKs this version confirms/refutes).

## 3. Write governance

Writer path: a single accessor class `_MerchantTruth` in
`receipt_dynamo/data` (pattern: `_merchant_font.py`) exposing only
`mint_version` (create manifest OPEN + components), `seal_version`,
`flip_active`, `add_proposal`, `resolve_proposal`, and readers. Deliberately
**no update/delete for version records** — immutability is enforced three
ways:

1. Every version-record put uses the data layer's existing default
   `ConditionExpression="attribute_not_exists(PK)"` (`base_operations`
   mixins — evaluated against the addressed item, so it is exactly
   create-only per (PK, SK)).
2. No mutating accessor exists to call.
3. `seal_version` conditionally requires `status=OPEN` and stamps the
   component hash list; after SEALED nothing can be added (component mint
   checks manifest `status=OPEN`).

Batch-tool-style safety (per the OCR-migration rollback tooling
conventions): the writer asserts the resolved table name matches the
intended env before any write (dev vs prod guard), all creates are
conditional, and every mint/seal/flip writes an `AUDIT#` record in the same
`TransactWriteItems` (writer identity, action, version, bundle_hash,
git_sha, hostname, run_id).

Writer identity: `written_by = {kind: "measurement_pipeline" |
"engine_config_sync" | "migration", name, version}`. Measured components
(typography, stylemap, layout, assets, catalog_snapshot, identity) accept
only `measurement_pipeline`/`migration`; the `flags` component accepts only
`engine_config_sync`.

**Engine flags separation (#1188 rule 2):** flags (`reverse_total`,
`dashed_separators`, condense-as-behavior, `face_source`, compose knobs…)
are authored in git — a per-merchant `engine_flags` block reviewed by PR —
and a sync tool snapshots them into `C#flags` at mint time with the file's
git SHA as provenance. Truth = measured; flags = decided. The bundle
versions both, so a render is reproducible from `{version}` alone, but the
write paths and reviewers differ.

**Version numbering:** mint reads the current max version (descending Query,
first item) and writes manifest v(max+1) with the conditional create; a race
loses the condition and retries. No counter item needed.

**Transact limits:** `TransactWriteItems` caps at 25 items (the data layer
already chunks at 25, `flattened_mixin.py`). Mint therefore writes immutable
components first (chunked, each conditional), then seals with a small
transaction on the manifest alone; the flip is its own 3-item transaction
(ConditionCheck manifest + Update pointer + audit put). Component writes
outside the seal transaction are safe precisely because unsealed versions
are invisible to readers — only SEALED versions can become ACTIVE.

**One TYPE per record class** is load-bearing, not cosmetic: the fleet query
sweeps `MERCHANT_TRUTH_ACTIVE` only because pointers carry their own TYPE —
a shared TYPE would drag every historical version through the GSITYPE query.
The same discipline keeps any future `RESTORABLE_TYPES`-style allowlist
precise.

**ACTIVE flip = the merge.** Sequence:

1. Pipeline mints + seals vN.
2. `merchant_truth_diff vM vN` renders a semantic per-component diff (skip
   identical hashes; layout: column moves in paper-width units;
   stylemap/flags: rule-level diff; assets: hash + metric deltas) — this is
   the review artifact.
3. Flip requires the manifest gate block green: `full_fidelity_eval` report
   reference + metrics PASS recorded on the manifest at seal time (no
   fidelity fix ships without a metric — same standing rule).
4. `flip_active` is a `TransactWriteItems`: ConditionCheck manifest vN
   exists AND `status=SEALED` AND gate=PASS, + Update on `TRUTH#ACTIVE`
   conditioned on `version = :expected_prev` (optimistic lock), + audit put.

A single-item pointer update means readers atomically see the old or new
bundle, never a mix; rollback is the same flip with `prev_version`. Who
flips: in dev, CI may auto-flip on gate PASS; in prod, the owner runs the
flip CLI after reading the diff (mirrors the existing dev→prod promotion
discipline). `fleet_status` turns red on any merchant whose ACTIVE
`gate_status != PASS` or whose `catalog_hash` drifted from the snapshot.

**Owner observations:** "Vons has bold category headers" → `add_proposal`
writes a PROPOSED record (free text + optional receipt refs, status OPEN).
The measurement pipeline treats OPEN proposals as targeting hints; when a
subsequent version's measurement confirms or refutes, `resolve_proposal`
stamps status + `resolved_by_version`, and the manifest lists it under
`confirmed_proposals`. Proposals never write truth components — the #1188
rule ("measurement confirmation, not assertion") is structural, not
procedural.

## 4. Migration map

| Current store | Target | Current readers → shim |
|---|---|---|
| `merchant_profiles.json` measured typography (bitmap_font face metrics, condense, bitmap_thin, section_scale) | `C#typography` | `scripts/render_synthetic_receipts.py`, glyphstudio `face_map`/`section_face_map` |
| `merchant_profiles.json` behavior flags (reverse_total, dashed_separators, heading_bleed_phrase, dash_around_phrases, face_source, compose blocks) | `C#flags` (git-sourced snapshot) | same readers; source file stays in repo |
| Published stylemap JSONs (today S3 via `MerchantFont.stylemap_s3_key`) | `C#stylemap` (inline if <300KB else S3 pointer) | `receipt_agent/.../rendering/receipt_stylemap.py`, `render_synthetic_receipts.py` |
| `layout_template` blocks (columns/sections/separators + measured provenance; 4 merchants so far) | `C#layout` — schema lifted verbatim from `glyphstudio/layout_template.py` output; its `measured` block becomes manifest provenance | `full_fidelity_eval --columns-source profile`; P3 `resolve_columns` |
| `MerchantFont` pointers + logo (s3_key, content_hash, cap_h, advance_ratio, logo_s3_key, logo_anchor) | `C#assets`: `{fonts: {face → {s3_key, content_hash, cap_h, advance_ratio, cache_filename}}, logo: {s3_key, content_hash, anchor}}` | `render_synthetic_receipts.py` font cache; `MerchantFont` kept during migration as the compile-output staging record the next mint reads — retire after P5 re-baseline |
| `MerchantCatalogItem` items | stay put; `C#catalog_snapshot` descriptor only | add-line-item augmentation unchanged |
| identity (exact merchant_name ↔ slug ↔ Places aliases) | `C#identity` | fixes MerchantFont-name vs catalog-slug split |

**Loader shim:** one `MerchantTruthLoader` (read ACTIVE → manifest →
components) with a read-through disk cache
`~/.cache/merchant_truth/{slug}/v{n}/{component}.json` keyed by
content_hash. Because versions are immutable the cache never invalidates;
only the ACTIVE read needs the network — one read per process, matching the
renderer's existing whole-registry-then-dict-lookups pattern, so no hot path
gains a network round-trip. The loader must preserve today's degraded-mode
semantics (the font fetch path never raises; CI renders with missing atlases
by design): mirror the `_ensure_font_cached` pattern — Dynamo → local cache
→ proceed-without, warning loudly. `--pin-version` gives reproducible
renders; offline falls back to the last cached ACTIVE. CI/offline dev use a
vendored fixture bundle.

**Identity migration:** three merchant-key encodings coexist today — exact
display name in `MerchantFont` PK, `UPPER_SLUG` in `ReceiptPlace` GSI1,
lower slug in the catalog. `MERCHANT_TRUTH#{slug}` picks the catalog's
lower-slug form and `C#identity` carries the full alias map (display name,
UPPER_SLUG, Places aliases). Migration keeps a compat read path for
`MerchantFont`'s exact-name PK until it retires.

**MCP surface:** any new truth accessor exposed through receipt-tools must
land in BOTH implementations (stdio `scripts/receipt_mcp_server.py` and the
Lambda `receipt_mcp_server_server.py`) or the tools silently diverge.

Migration is incremental: mint v1 per merchant from today's files via a
one-shot `migration` writer (provenance = repo git SHA), flip ACTIVE, then
convert readers one store at a time — layout first, then stylemaps,
calibrations, catalog snapshot, typeface registry as each measurement
pipeline matures. P3's compose engine reads through the loader from day one
to avoid a schema break.

## 5. Risks / what stays out of Dynamo

Out: npz atlases, logo PNGs, eval sheets/reports → S3 + `{s3_key,
content_hash}` pointers; glyph stroke sources and skeletons → git; engine
flag source files → git (snapshotted only).

Risks:

1. **dev→prod:** `reconcile_dev_to_prod.py` fingerprints only `IMAGE#`
   partitions, so `MERCHANT_TRUTH#` is invisible to it — safely outside the
   `RESTORABLE_TYPES` deletion gate (good: truth stays out of image
   partitions), but also **never mirrored**. Today merchant fonts reach prod
   only by re-running the publish against the prod table. A merchant-truth
   reconcile leg keyed on content_hash/version (trivial given GSITYPE
   enumeration) must land before the first prod mint, or dev/prod drift is
   guaranteed.
2. **Hand-edit temptation:** single-owner project, so enforcement is
   code-path + conditional-create + audit, not IAM — acceptable; the audit
   trail makes cheating visible.
3. **Stylemap growth past 400KB** → automatic S3 spillover in the writer.
4. **Offline render drift** if ACTIVE moved while a machine was cached —
   recipe-hash embedding of `bundle_hash` catches it at eval.
5. **Version sprawl** — versions are tiny (KBs); keep all, they ARE the
   provenance record; a purge tool only for dev-table hygiene.

Precedents: `MerchantFont` (S3 pointer + content_hash + source_commit +
conditional semantics), `MerchantCatalogItem` (slug PK, item-per-product,
GSI1/type-constant), GSITYPE fleet enumeration (`infra/dynamo_db.py`),
`base_operations` default conditional create, `reconcile_dev_to_prod`
promotion discipline.

## 6. Open questions

1. Should `C#flags` instead stay entirely in git with the manifest only
   recording its git SHA + hash? Purer separation, but breaks "render
   reproducible from Dynamo alone". Current lean: snapshot into Dynamo.
2. Proposals under the merchant PK (current choice — merchant-scoped and
   cheap) vs a global `PROPOSAL#` partition.
3. `MerchantFont` retirement timing (proposed: after P5 re-baseline).
