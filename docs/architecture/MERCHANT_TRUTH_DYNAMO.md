# Merchant Truth in DynamoDB

Design for #1188 P4: move per-merchant "truth" (measured typography, stylemaps,
layout templates, asset pointers, engine flags, catalog snapshots) out of
`scripts/merchant_profiles.json` and scattered S3 JSONs into versioned,
content-hashed, provenance-carrying records in the existing single table,
using proper DynamoDB access patterns.

Status: DESIGN v3.1 — schema-evolution amendment; core v3 unchanged. Review
rounds: 2 (cap reached per #1190 polish policy). Remaining spec ambiguity is
settled at implementation time in #1188 P4 code+tests. The v3.1 amendment is
§7 only; §§0–6 are the frozen v3 text.

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
| Renderer (`scripts/render_synthetic_receipts.py`) | one bundle read at render start (today: whole-registry in-process cache at first call, then dict lookups; font/stylemap/logo point-read only on local cache miss — the current fetch path returns any existing local file **without** hash verification and swallows all errors, `:1140-1203`; the new loader replaces that with fail-closed hash-verified loads, §4) | read ACTIVE → manifest → components, once per process, through the loader cache |
| Alias resolution (today a linear scan over profile `aliases`) | name → canonical merchant | denormalized alias map on the `MERCHANT_TRUTH_ACTIVE` pointers (one GSITYPE fleet query; finding 1) — `C#identity` is the versioned record of the mapping, not the lookup path |
| Measurement writers (glyphstudio stylescan/styleagg/layout_template) | put new version with provenance; today `layout_template.py` records a `tool_dirty` boolean but does **not** guard on it — it overwrites `merchant_profiles.json` regardless (`layout_template.py:240-258,279-292`), so there is no real "versioning" today | `mint_version`/`seal_version` with explicit conditional creates (§3); the mint writer enforces `dirty=false` rather than merely stamping it |
| Eval (`full_fidelity_eval.py`) | get active layout template by merchant, schema-validated | ACTIVE → `C#layout` |
| Fleet status | list all merchants with truth + active summary — **does not exist as a tool today** (enumeration = profiles keys + `env.mjs FONT_MERCHANTS`); this design creates it | one GSITYPE query on `MERCHANT_TRUTH_ACTIVE` |
| Compose engine | pinned catalog for the active version — today re-derives the catalog per run | `C#catalog_snapshot` (full inlined catalog); `MerchantCatalogItem` stays the mutable authoring/staging partition |
| dev→prod mirror (`reconcile_dev_to_prod.py`) | copy truth to prod | new reconcile leg (§5 risk 1) |
| Agent sessions (owner observations) | propose / review / resolve | `PROPOSED#` records + `resolve_proposal`; TTL usable for expiring drafts |

Measured sizes: the `refactor-p1` profile registry is 28,056 bytes total for
16 merchants after four layouts (the 15.6KB figure was an older `render-redo`
snapshot); largest single profile 3,716 bytes, largest `layout_template`
block 2,343 bytes; published stylemaps 749–8,972 bytes with a 41,133-byte
outlier (Target). Every component fits a single item with wide margin under
400KB, so the S3-spillover path is a safety valve, not the normal case. None
of the current objects exercises spillover.

## 2. Entity set and key design

Single table, existing GSIs only (GSI1..GSI4 + GSITYPE with hash=TYPE, per
`infra/dynamo_db.py`). Merchant slug via the existing `slugify_merchant()`
(`receipt_dynamo/entities/merchant_catalog_item.py`) — this also fixes the
current split-brain where `MerchantFont` keys on exact `merchant_name` while
catalogs key on slug; the identity component records the mapping.

**PK = `MERCHANT_TRUTH#{slug}`** for all records below.

| Record | SK | TYPE | Notes |
|---|---|---|---|
| Active pointer | `TRUTH#ACTIVE` | `MERCHANT_TRUTH_ACTIVE` | `{version, bundle_hash, activated_at, activated_by, gate_status, prev_version}` + denormalized fleet summary fields + `normalized_aliases` (the current alias→slug map for this merchant, so one GSITYPE fleet read yields the whole fleet's alias map; finding 1) |
| Version manifest | `TRUTH#v{n:010d}#MANIFEST` | `MERCHANT_TRUTH_MANIFEST` | component list w/ per-component `content_hash`, `bundle_hash`, `status` (single conditional OPEN→SEALED transition), mint/seal-level provenance block, gate results |
| Component | `TRUTH#v{n:010d}#C#{component}` | `MERCHANT_TRUTH_COMPONENT` | component ∈ identity, typography, stylemap, layout, assets, flags, catalog_snapshot; **immutable**, carries its own per-component provenance (finding 6) |
| Proposal | `PROPOSED#{iso_ts}#{claim-slug}` | `MERCHANT_TRUTH_PROPOSAL` | owner observation, append-only body with a mutable `status` attribute: `OPEN` → `MEASURED_IN_CANDIDATE` → `EFFECTIVE` (finding 7) |
| Audit | `AUDIT#{iso_ts}#{ulid}` | `MERCHANT_TRUTH_AUDIT` | one per mint/seal/flip/rollback |

Key-grammar payoffs (all plain Query, no GSI needed):

- Full bundle read: Query `begins_with(SK, "TRUTH#v0000000042#")`.
- Latest versions: Query `begins_with(SK, "TRUTH#v")` with
  `ScanIndexForward=False` — the 10-digit zero-padded version (`v{n:010d}`,
  finding 9) makes lexicographic = numeric across the entire realistic range
  (a 5-digit width sorts `v99999` after `v100000` and was wrong); `ACTIVE`
  sorts before `v` so it never pollutes; within a version `C#` < `MANIFEST`
  so descending yields the manifest first.
- Everything about a merchant: Query PK.

GSI usage: fleet enumeration (`fleet_status`) = one GSITYPE query for
`TYPE = MERCHANT_TRUTH_ACTIVE` — the pointer denormalizes
`{slug, version, bundle_hash, gate_status, activated_at, normalized_aliases}`
so both fleet status **and** alias resolution need no fan-out. Alias lookup
is the executable first hop the earlier design lacked (finding 1): the single
fleet query returns every ACTIVE pointer, and the union of their
`normalized_aliases` maps is the name→slug table; the loader builds it before
any canonical slug is known, then reads ACTIVE/components for the resolved
slug. Listing OPEN proposals: GSITYPE on
`MERCHANT_TRUTH_PROPOSAL` (filter `status=OPEN`; volume is tiny). No new GSI
keys are required. An optional GSI1 mirror (`GSI1PK=MERCHANT_TRUTH`,
`GSI1SK=SLUG#{slug}#v{n}` on manifests, following `MerchantCatalogItem`'s
convention) is deferred — GSITYPE covers every known access pattern.

**400KB limit:** component-per-item, never a monolith. Measured components
today are small (largest `layout_template` block ≈ a few KB; stylemaps tens
of KB). The writer enforces: serialized payload > 300KB → store
`payload_s3_key` + `payload_size` instead of inline `payload`;
`content_hash` is always inline so gates and diffs never need S3.

**Payload representation (hash stability, learned live in G1):** the inline
`payload` attribute stores the component's **canonical JSON string** — the
exact bytes `content_hash` was computed over — never a native
AttributeValue tree. DynamoDB normalizes number representations (`0.0` is
stored and returned as `0`), which silently changes the canonical JSON of a
round-tripped native map and breaks read-back hash verification (the G1
costco mint failed its seal on exactly this, via `bitmap_thin: 0.0`).
Strings round-trip verbatim; readers `json.loads` the string and re-verify
the hash, and reject legacy AttributeValue-encoded payloads outright.

**The catalog inlines into the version** (revised in #1193 round-1 review).
`C#catalog_snapshot` carries the full sorted, normalized product records
plus `{item_count, catalog_hash, as_of}` — catalogs are tiny relative to
400KB, and a descriptor-only snapshot would be detection-only: it could
notice drift in the mutable catalog rows but never reproduce the catalog
that was active when the version sealed, breaking reproducibility from the
sealed manifest. Canonical ordering/normalization is specified with the
component schema and item size is measured before assuming a catalog is
"tiny". `MerchantCatalogItem` (PK=`MERCHANT_CATALOG#{slug}`)
remains as the mutable measurement/authoring staging partition; mint copies
its complete state into the immutable bundle, and compose reads the
versioned snapshot, not the live rows. The >300KB S3 spillover valve applies
if a catalog ever outgrows one item; spilled payloads stay content-hashed
inside the versioned closure.

**Content hashing:** per-component sha256 of canonical JSON (sorted keys,
compact separators); `bundle_hash` = sha256 of the sorted
`"{component}:{hash}"` lines. Every external S3 object is independently
hash-addressed — fonts, stylemap, AND logo. Caution (#1193 round-1 review):
today's `MerchantFont` rows hash only the NPZ bytes; `stylemap_s3_key`
and `logo_s3_key` are bare pointers. The migration writer must therefore
fetch the current stylemap/logo bytes, compute sha256, and verify them
before sealing — never reuse the NPZ hash — and the loader verifies every
download against its hash. A missing or mismatched hash fails
seal/promotion; it never silently downgrades.

Renders and evals record `{slug, version, bundle_hash}` into the recipe
hash. Recording the tuple alone does **not** catch a stale ACTIVE cache
(finding 5): it proves only which bundle was used, not that the bundle was
current. The gate closes the gap by *comparing* the render tuple against an
expected ACTIVE (or pinned) tuple captured for the run — see the
truth-resolution modes in §4. So "stale sheets structurally impossible"
holds only when that comparison is wired into the freshness gate, not from
stamping the tuple for free.

**Provenance is per-component, required (finding 6).** A single manifest
`written_by`/`git_sha` cannot faithfully describe a version that combines,
say, measured layout from commit A with reviewed flags from commit B, so
each component item carries its own provenance block: `source_kind`
(`measurement` | `engine_config` | `migration`), `written_by` (writer
identity, §3), `source_path`/`source_object` (the file or S3 object it came
from), `source_hash`/`git_sha`, `pipeline` + `pipeline_version` (e.g.
`glyphstudio.layout_template@1`), `measured_at`, and `source_receipt_keys`
where applicable (measured components). The manifest keeps only mint/seal-
level provenance: who minted/sealed, when, the mint `run_id`, the aggregate
`git_sha` of the mint tool, and `confirmed_proposals` (proposal SKs this
version measures). During v1 migration a component may set
`source_kind=migration` with an explicit `provenance_completeness=legacy`
marker rather than inventing receipt keys or measurement times it does not
have.

## 3. Write governance

Writer path: a single accessor class `_MerchantTruth` in
`receipt_dynamo/data` exposing only `mint_version` (create manifest OPEN +
components), `seal_version`, `flip_active`, `add_proposal`,
`resolve_proposal`, and readers. Deliberately **no update/delete for
component records.**

There is no reusable "create-only default" to inherit (finding 2). The
`FlattenedStandardMixin._add_entity` path used by `_MerchantFont` defaults
`condition_expression=None` and writes **without** a condition — that is why
the MerchantFont pointer overwrites — so this accessor does **not** cite it
as a create-only precedent. Instead, every write in `_MerchantTruth` passes
its `ConditionExpression` **explicitly**, and the generated DynamoDB request
(key, condition, and expression attribute values) is asserted in a unit
test for each operation.

The real invariant, stated exactly:

1. **Component items are immutable** — each component Put carries
   `ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)"`
   and there is no update/delete accessor for them.
2. **The manifest has exactly one conditional `OPEN→SEALED` transition** —
   `seal_version` Updates the manifest under `status = :open`, stamps the
   verified component hash list, and thereafter nothing can be added
   (component mint conditions on manifest `status=OPEN`). No other manifest
   mutation exists.
3. **ACTIVE is optimistically mutable** — `flip_active` Updates the single
   pointer under an expected-state condition (initial vs. subsequent, §3
   flip).
4. **Proposals are append-only bodies with a single mutable `status`
   attribute** (`OPEN` → `MEASURED_IN_CANDIDATE` → `EFFECTIVE`, finding 7);
   the free-text observation and receipt refs never change after creation.

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
versions both, so a render is reproducible from the **versioned manifest
plus its verified content-addressed dependencies** — not from the `{version}`
string alone, since external bytes still live in S3 and vendored TTF/stroke
sources still live in git (finding 12); the closure is exactly the manifest
plus every hash-verified dependency it names. The write paths and reviewers
for measured vs. decided components differ.

**Version numbering:** mint reads the current max version (descending Query,
first item) and writes manifest v(max+1) with the conditional create; a race
loses the condition and retries. No counter item needed.

**Atomic mint** (arithmetic corrected per finding 8): the normal bundle —
OPEN manifest + seven components + mint audit = nine actions, worst-case
~2.05 MiB of payload — is a **single `TransactWriteItems`**, well inside
DynamoDB's actual service limits of **100 actions and 4 MiB per
transaction** (all puts conditional; the accessor asserts assembled action
count and aggregate size against those service limits before sending). No
partial OPEN versions exist on the normal path. The repo's
`flattened_mixin` helper chunks at **25** and sends *independent*
transactions — that 25 is a legacy helper batch size, **not** a DynamoDB
limit, and it does not give atomicity — so this accessor **bypasses that
helper and issues its own `TransactWriteItems`** directly. The resumable
overflow protocol is reserved for the case where the bundle actually exceeds
the **service** limits (>100 actions or >4 MiB), not >25 items: the OPEN
manifest records expected component names/hashes/count and a mint run ID;
chunks use idempotent conditional puts; seal verifies the complete expected
set; a retry resumes the same version rather than allocating a new one. The
flip stays its own 3-item transaction (ConditionCheck manifest + Update
pointer + audit put).

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
4. `flip_active` is a `TransactWriteItems` that always ConditionChecks
   manifest vN exists AND `status=SEALED` AND gate=PASS, Updates
   `TRUTH#ACTIVE`, and writes an audit put. There are **two distinct
   activation operations** (finding 4), because the ACTIVE item does not
   exist before the first flip and so has no `version` attribute to match:
   - **Initial activation** — a conditional **Put** of `TRUTH#ACTIVE`
     guarded by `attribute_not_exists(PK) AND attribute_not_exists(SK)`.
     Exactly one of two concurrent first flips can win; the loser's
     condition fails and it retries as a subsequent activation.
   - **Subsequent activation** — an **Update** of `TRUTH#ACTIVE`
     conditioned on the exact expected prior state
     `version = :expected_prev AND bundle_hash = :expected_prev_hash`
     (optimistic lock).
   Retry is idempotent: if the pointer already names the target
   `{version, bundle_hash}` the operation is a no-op success rather than a
   condition failure. A test drives **two concurrent first flips** and
   asserts exactly one wins and the other converges without a second ACTIVE.

A single-item pointer update means readers atomically see the old or new
bundle, never a mix; rollback is the same subsequent-activation flip with
`prev_version` as the target. Flip and rollback also **reconcile proposal
effectivity** (finding 7): after the pointer moves, proposals recorded as
`MEASURED_IN_CANDIDATE` by the now-ACTIVE version are derived `EFFECTIVE`,
and proposals whose only measuring version is no longer ACTIVE revert out of
`EFFECTIVE`. Who flips: in dev, CI may auto-flip on gate PASS; in prod, the
owner runs the flip CLI after reading the diff (mirrors the existing
dev→prod promotion discipline). `fleet_status` turns red on any merchant
whose ACTIVE `gate_status != PASS` or whose `catalog_hash` drifted from the
snapshot.

**Owner observations:** "Vons has bold category headers" → `add_proposal`
writes a PROPOSED record (free text + optional receipt refs, status `OPEN`).
The measurement pipeline treats OPEN proposals as targeting hints. A
proposal moves through three states (finding 7), which separates *measured
in a candidate* from *effective in active truth*:

- `OPEN` — awaiting measurement.
- `MEASURED_IN_CANDIDATE` — stamped at **seal** time when a sealed version
  measures (confirms or refutes) the proposal, with `resolved_by_version`;
  the sealed manifest records it under `confirmed_proposals`. This is *not*
  effectivity — the version may still fail its gate or never be activated.
- `EFFECTIVE` — **derived**, not written directly: a proposal is effective
  exactly when its `resolved_by_version` is the currently ACTIVE version.
  Flip/rollback reconciles this (see the flip sequence). So a version that
  confirms a proposal but never becomes ACTIVE never makes it effective, and
  it stays visible in the OPEN/candidate targeting queue until an activated
  version measures it.

Proposals never write truth components — the #1188 rule ("measurement
confirmation, not assertion") is structural, not procedural.

## 4. Migration map

**Normative crosswalk requirement (finding 3).** The table below is a
*summary*, not the migration spec. Migration is driven by a **machine-checked
leaf-by-leaf crosswalk**: every source leaf path — across all 16 profiles in
`scripts/merchant_profiles.json`, every `MerchantFont` metadata field
(`s3_bucket`, `source_commit`, `compiled_at`, `pitch_check`, `glyph_count`,
…), and every published stylemap/logo object — is enumerated and classified
as exactly one of `measured`, `decided`, `asset`, `identity`, or
`discarded-with-reason`, each with an explicit destination. **Migration
FAILS on any unmapped or unknown leaf** — no silent fallback to an old file
or default. In particular the complete nested `header` and `graphics`
objects (including `footer_codes`, `footer_qr_only`, `inbody_barcode`), the
runtime typography leaves (`font`, `stroke`, `display_headings`, the
date/dash/reverse controls, `mixed_layout`, `condense_glyphs`,
`ocr_font_sizing`, `bitmap_cap_ratio`, `ocr_cap_height_ratio`, `pitch_ratio`,
`ink`, `bitmap_glyph_vscale`), and the profile/provenance comment leaves are
each mapped or explicitly discarded-with-reason. v1 **preserves the whole
nested `header`/`graphics`/flag payload verbatim** unless a *tested*
normalization covers every field it touches. The stylemap schema is
heterogeneous (most files `{version,source,sections}`; Target begins
`{receipts_used,files_scanned,letters_by_section,sections}` with no
`version`/`source`), so migration either accepts the legacy schema or applies
a tested normalization — never assumes one shape.

**Missing-asset plan.** Only 13 of 16 merchants have a current `MerchantFont`
row today; **Amazon Fresh, Smith's, and Dollar Tree have none** (and Smith's
has a local stylemap but no published current stylemap). These three must
**publish or import** their font/stylemap/logo assets — computing and
verifying independent hashes — **before their v1 seals**; v1 must not mint a
claimed asset closure it cannot actually satisfy. Migration follows the
current Dynamo pointer (or an explicitly chosen input), never an inferred
"latest" S3 prefix.

| Current store | Target | Current readers → shim |
|---|---|---|
| `merchant_profiles.json` measured typography (bitmap_font face metrics, condense, bitmap_thin, section_scale) | `C#typography` | `scripts/render_synthetic_receipts.py`, glyphstudio `face_map`/`section_face_map` |
| `merchant_profiles.json` behavior flags (reverse_total, dashed_separators, heading_bleed_phrase, dash_around_phrases, face_source, compose blocks) | `C#flags` (git-sourced snapshot) | same readers; source file stays in repo |
| Published stylemap JSONs (today S3 via `MerchantFont.stylemap_s3_key`) | `C#stylemap` (inline if <300KB else S3 pointer) | `receipt_agent/.../rendering/receipt_stylemap.py`, `render_synthetic_receipts.py` |
| `layout_template` blocks (columns/sections/separators + measured provenance; 4 merchants so far) | `C#layout` — schema lifted verbatim from `glyphstudio/layout_template.py` output; its `measured` block feeds the **`C#layout` component's own provenance** (finding 6). Note the persisted layout output carries receipt count + tool SHA + dirty boolean but no `source_receipt_keys`/`measured_at`, so v1 layout provenance is marked `legacy` rather than inventing receipt keys | `full_fidelity_eval --columns-source profile`; P3 `resolve_columns` |
| `MerchantFont` pointers + logo (s3_key, content_hash, cap_h, advance_ratio, logo_s3_key, logo_anchor) | `C#assets`: `{fonts: {face → {s3_key, content_hash, cap_h, advance_ratio, cache_filename}}, logo: {s3_key, content_hash, anchor}}` | `render_synthetic_receipts.py` font cache; `MerchantFont` kept during migration as the compile-output staging record the next mint reads — retire after P5 re-baseline |
| `MerchantCatalogItem` items | stay put as authoring/staging; mint copies full state into `C#catalog_snapshot` | add-line-item augmentation unchanged; compose switches to the versioned snapshot |
| identity (exact merchant_name ↔ slug ↔ Places aliases) | `C#identity` (versioned record of the mapping) **plus** the normalized alias→slug map denormalized onto `TRUTH#ACTIVE` for lookup (finding 1) | fixes MerchantFont-name vs catalog-slug split |

**Loader shim:** one `MerchantTruthLoader` (resolve alias → slug → ACTIVE →
manifest → components) with a read-through disk cache
`~/.cache/merchant_truth/{slug}/v{n}/{component}.json` keyed by
content_hash. Because component items are immutable the **component** cache
never invalidates. The **ACTIVE pointer is never cached across the network**
(finding 5): when online, the loader always re-reads `TRUTH#ACTIVE` so a flip
that happened while a process was warm is seen; only the immutable components
are cached. That is still one ACTIVE read per process, matching the
renderer's whole-registry-then-dict-lookups pattern, so no hot path gains a
per-lookup round-trip.

**Four truth-resolution modes (finding 5):**

- `pinned` — `--pin-version` selects an exact `{version, bundle_hash}`. The
  eval compares the realized render tuple against the requested pin; a
  mismatch fails.
- `online-active` — resolve ACTIVE fresh; the eval compares the realized
  render tuple against an **expected ACTIVE tuple captured once for the run**
  (not merely stamps what it used), so a stale warm/offline cache that
  rendered an older version is detected.
- `offline-fallback` — no network; use the last cached ACTIVE. This mode is
  explicit and **CI rejects an unrequested fallback**: a CI run that intended
  `online-active` and silently degraded to a cached pointer fails.
- `fixture` — CI/offline dev renders a vendored fixture bundle by design.

**Bundle load is paginated and fail-closed (finding 10).** The full-bundle
Query is paginated to exhaustion (`LastEvaluatedKey`) — seven inline ~300KB
components can span multiple 1MiB Query pages, so a single-page read would
silently drop components. The loader then verifies the loaded set against the
manifest's declared **exact component names, count, per-component hashes, and
`bundle_hash`**. In `pinned` and CI modes the load **fails closed** on any
missing page, missing component, or hash mismatch — it does not proceed
without. The legacy `_ensure_font_cached` behavior (return any local file
unverified, swallow all errors) is **not** carried over. A degraded/offline
render may still proceed, but it yields an artifact explicitly marked
`incomplete=true` that **cannot pass gates and never claims the sealed
recipe/bundle hash** as fully realized.

**Identity migration:** three merchant-key encodings coexist today — exact
display name in `MerchantFont` PK, `UPPER_SLUG` in `ReceiptPlace` GSI1,
lower slug in the catalog. `MERCHANT_TRUTH#{slug}` picks the catalog's
lower-slug form and `C#identity` is the versioned record of the full alias
map (display name, UPPER_SLUG, Places aliases); the **normalized** alias→slug
entries are additionally denormalized onto `TRUTH#ACTIVE` so lookup is served
by the single fleet query before any canonical slug is known (finding 1).
Alias collisions across merchants are rejected in the same validation path as
flip. Migration keeps a compat read path for `MerchantFont`'s exact-name PK
until it retires.

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
content_hash}` pointers (also: binaries are paid-font-derived and
deliberately out of git); glyph stroke sources, skeletons, and vendored TTF
faces → git; engine flag source files → git (snapshotted only). Stylemap
gets no double-master: once its published form is a Dynamo truth item, the
git copy in `fonts/<m>/stylemap.json` is strictly a build input (like glyph
sources), never read at render time.

Risks:

1. **dev→prod:** `reconcile_dev_to_prod.py` builds its fingerprint from
   type-scoped scans but plans and guards at `IMAGE#` granularity (its guard
   queries `PK=IMAGE#{id}`), so `MERCHANT_TRUTH#` is invisible to it —
   safely outside the `RESTORABLE_TYPES` deletion gate (good: truth stays out
   of image partitions), but also **never mirrored**. Today merchant fonts
   reach prod only by re-running the publish against the prod table. Per the
   #1193 review: implement a **separate, additive, fail-closed promotion
   leg** (do NOT add truth TYPEs to the image-scoped `RESTORABLE_TYPES`, and
   do not describe this as an extension of image reconcile). For each
   explicitly selected merchant/version it:
   - (a) reads the SEALED dev manifest and its complete immutable closure
     (components, inlined catalog, S3 assets);
   - (b) verifies component hashes, `bundle_hash`, gate PASS, and S3 bytes
     before any prod write;
   - (c) **copies the S3 assets copy-and-verify into the prod artifact
     namespace** (finding 11). The asset keys are content-addressed — the key
     embeds the content hash — so promotion preserves the same keys and a
     pointer rewrite is a **bucket swap only**; the hashes are unchanged and
     are **re-verified after the copy**. It never leaves a prod Dynamo record
     pointing into a dev bucket or at a key absent in prod;
   - (d) imports into prod **only the selected SEALED bundle plus a prod
     promotion audit** — one transaction for the normal 9-item bundle,
     conditional creates, failing on version conflict rather than
     renumbering. OPEN versions, proposals, dev audit history, and
     `TRUTH#ACTIVE` are **never** imported;
   - (e) verifies the prod closure, then stops. The owner runs the reviewed
     prod `flip_active` after inspecting the diff — prod normally promotes
     the reviewed sealed dev bundle rather than independently minting a
     logically equivalent version.

   Guard: the writer must have a hard prod-account/table guard and no
   reachable prod write path from dev mint/load/flip. This leg must land
   **before the first prod promotion/import or prod ACTIVE flip** (not merely
   "before the first prod mint").
2. **Hand-edit temptation:** single-owner project, so enforcement is
   code-path + conditional-create + audit, not IAM — acceptable; the audit
   trail makes cheating visible.
3. **Stylemap growth past 400KB** → automatic S3 spillover in the writer.
4. **Offline render drift** if ACTIVE moved while a machine was cached —
   caught not by stamping the stale tuple (that only records what was used)
   but by the `online-active` eval comparing the realized tuple against the
   expected ACTIVE tuple captured for the run, and by CI rejecting an
   unrequested offline fallback (finding 5).
5. **Version sprawl** — versions are tiny (KBs); keep all, they ARE the
   provenance record; a purge tool only for dev-table hygiene.

Precedents: `MerchantFont` (S3 pointer + content_hash + source_commit —
note its pointer write is unconditional/overwriting, so it is a shape
precedent, **not** a create-only one), `MerchantCatalogItem` (slug PK,
item-per-product, GSI1/type-constant), GSITYPE fleet enumeration
(`infra/dynamo_db.py`), `reconcile_dev_to_prod` promotion discipline. This
design supplies its own explicit, unit-tested `ConditionExpression` on every
write rather than inheriting a create-only default that the data layer does
not provide (finding 2).

## 6. Decisions (resolved per #1193 design review)

1. **`C#flags` snapshots into Dynamo.** Git stays the reviewed source of
   authority; the snapshot records source path, git SHA, a `dirty=false`
   requirement, and content hash. The immutable snapshot is what makes a
   pinned version reproducible without checking out a second repo state.
2. **Proposals live under the merchant PK.** Merchant-scoped review is a
   plain Query; GSITYPE covers the small global OPEN queue. A global
   partition would add a shared namespace with no current consumer.
3. **`MerchantFont` retires after P5 re-baseline, gated on exit criteria,
   not the milestone date:** every merchant has a sealed+active truth
   version with distinct NPZ/stylemap/logo hashes, dual-read parity passes,
   full-fidelity re-baseline done, rollback window tested, and all
   consumers on `MerchantTruthLoader` before the compat reader is removed.
4. **Alias resolution is an in-memory map with an executable fetch path**
   (finding 1). The normalized alias→slug entries are **denormalized onto
   each `TRUTH#ACTIVE` pointer**, so the single GSITYPE fleet query that
   already backs `fleet_status` also yields the whole alias map *before* any
   canonical slug is known — closing the earlier circular dependency on
   `C#identity` (which remains the versioned record of the mapping, not the
   lookup path). Mint/fleet validation rejects aliases that are ambiguous
   across merchants, in the same validation path as flip. Globally
   addressable `ALIAS#<normalized-name>` records are an optional later step,
   only if scale or a direct point-read consumer requires it.

## 7. Schema evolution (v3.1)

Amendment for the Costco complete-in-Dynamo pilot (v2 bundles). §§0–6 are
unchanged v3 text; every rule below is additive. Ratified per the plan
(`humble-skipping-quilt`) at the W-A owner review.

### 7.1 Payload-evolution rules

Component `payload` values are opaque canonical JSON (`Any` at the entity
layer): a payload's **shape MAY evolve across bundle versions** without a
contract revision. What is frozen is the component **set** — exactly the 7
names in `COMPONENT_NAMES` (identity, typography, stylemap, layout, assets,
flags, catalog_snapshot). Adding (or removing) a component is a **major
contract revision**, not a payload evolution: it trips the four exact-set
gates plus the diff tool's fixed review order —

1. `MerchantTruthManifest.__post_init__`
   (`receipt_dynamo/entities/merchant_truth.py` — "manifest must declare the
   exact component set");
2. `_MerchantTruth.mint_version`
   (`receipt_dynamo/data/_merchant_truth.py` — "mint requires exactly one of
   every declared component");
3. the `MerchantTruthLoader` bundle verification
   (`receipt_dynamo/merchant_truth_loader.py` — "loaded component
   names/count do not match the contract");
4. the `merchant_truth_diff` contract check
   (`scripts/merchant_truth_diff.py`) — plus its module-level
   `assert set(COMPONENT_ORDER) == COMPONENT_NAMES`.

Rules for any payload shape evolution:

- All four gates above (and every existing schema validator) must keep
  passing on both old and new payloads — evolutions are additive or
  tolerated-unknown-key changes, never breaking rewrites.
- Old sealed versions are immutable and are never rewritten to the new
  shape; readers must accept every shape this section has ever sanctioned.
- Every shape evolution is **sanctioned in this section first** (docs PR
  precedes code, invariant 4 of the plan).

### 7.2 Layout variants: `template.variants[]`

The layout component learns receipt-format variants (e.g. Costco register
vs. self-checkout) **additively**. The template keeps `version: 1`; the
existing top-level `columns` / `sections` / `separators` remain exactly what
they are today and are now defined as the **DEFAULT variant**. A new
optional key is added:

```json
"variants": [
  {
    "variant_id": "self-checkout",
    "classifier_hint": "...",
    "columns": [...],
    "sections": [...],
    "separators": [...],
    "support": 7,
    "source_receipt_keys": ["IMAGE#...|RECEIPT#..."]
  }
]
```

- `support` (receipt count backing the variant) and `source_receipt_keys`
  are **required** on every variant entry — no unprovenanced variants.
- Readers (eval `profile_columns`, `merchant_truth_diff`'s `diff_layout`,
  and any future variant-aware renderer) select a variant by
  `classifier_hint`; when no hint matches — or the reader is
  variant-unaware — they fall back to the top-level DEFAULT variant.
  Existing validators tolerate the unknown `variants` key, so v1 bundles
  and variant-unaware readers are unaffected.
- The template `version` stays `1`; `variants` presence/absence is the only
  discriminator.

### 7.3 Version-number readers

§3 already promises "mint reads the current max version (descending Query,
first item)". v3.1 sanctions that read as two named accessors on
`_MerchantTruth`:

- `get_latest_merchant_truth_version(slug, *, sealed_only)` — descending
  Query on `begins_with(SK, "TRUTH#v")`, first manifest. With
  `sealed_only=True` it returns the highest **SEALED** version (what
  promotion/diff/mint-from want); otherwise the highest version of any
  status, OPEN included (what mint allocation must see so it never collides
  with an in-flight OPEN version).
- `next_mint_version(slug)` — `get_latest…(sealed_only=False) + 1` (1 when
  no versions exist), still guarded by the conditional manifest create: a
  race loses the condition and retries with a re-read.

Version numbers are **never reused**. `cleanup_merchant_truth_open_version`
may delete an abandoned OPEN version, leaving a gap in the sequence; gaps
are legal and expected, and readers must not assume density — "latest" is
whatever the descending Query returns, not `count`.

### 7.4 Eval→seal gate bridge and the PASS_WITH_GAPS policy

`full_fidelity_eval` emits `checks["overall"] ∈ {PASS, PASS_WITH_GAPS,
FAIL}`, while `seal_version` derives its gate from `gate_results`
(`_derive_gate_status`: explicit `status`/`passed`, fail-closed on
ambiguity). A bridge adapter maps one onto the other; policy:

- `PASS` → seals: `gate_results = {status: "PASS", passed: true, overall:
  "PASS", …}`.
- `PASS_WITH_GAPS` → **seals**: the bridge presents a passing seal signal
  (`status: "PASS", passed: true`) while recording `overall:
  "PASS_WITH_GAPS"` and the gap list **verbatim** in `gate_results` on the
  manifest AND in the gate record (§7.5). Gaps are never summarized away.
- `FAIL` → **blocks the seal**: the version stays OPEN, and the gate record
  written for the failing run is the work list for closing it.

Rationale: sealing on PASS_WITH_GAPS is safe because **flips stay
owner-gated** — a sealed-with-gaps version still cannot become ACTIVE
without the owner reading the diff and the recorded gaps.

### 7.5 `MERCHANT_TRUTH_GATE` record type

Eval/gate history moves from files-only into a queryable record class under
the merchant partition:

| Record | SK | TYPE |
|---|---|---|
| Gate run | `GATE#{run_at_iso}#v{n:010d}` | `MERCHANT_TRUTH_GATE` |

- **PK** = `MERCHANT_TRUTH#{slug}`, same partition as the truth records;
  `GATE#` sorts before `PROPOSED#` and `TRUTH#`, so the existing
  `begins_with(SK, "TRUTH#…")` queries are untouched. The version segment
  uses the same `v{n:010d}` zero-padded encoding as the key grammar (§2).
- **Own TYPE** `MERCHANT_TRUTH_GATE`, per the one-TYPE-per-record-class rule
  (§3): fleet-wide gate-history enumeration is one GSITYPE query and never
  drags other record classes.
- **Payload:** `{run_at, bundle: {version, hash}, eval_git_sha, overall,
  per_metric verdicts, evidence_refs, receipt_tested}` — enough to answer
  "what gated this bundle, when, at which eval code, and with which
  evidence" without opening files.
- **Append-only:** conditional create
  (`attribute_not_exists(PK) AND attribute_not_exists(SK)`); no
  update/delete accessor, same discipline as components.
- **Evidence sheets stay in S3/files** — `evidence_refs` are pointers, per
  the §0 non-goal on binaries.
- **Write governance:** the writer passes the same `expected_table_name`
  env guard as every `_MerchantTruth` write; the eval writes gate records
  only when explicitly enabled (`--write-gate-record`, default off) and,
  until a prod promotion policy for gate history exists, writes are
  **dev-pinned** — gate records are not part of the promoted bundle closure
  (§5 risk 1 imports "only the selected SEALED bundle plus a prod promotion
  audit"; that list does not grow here).

Gate history is a **record type, not an 8th component** (§7.1): it is
per-run evidence about a bundle, not part of the bundle's immutable
closure, and it must not perturb `bundle_hash` or the exact-set gates.

### 7.6 Measurement-mint provenance

v2+ mints are **real** mints, not migrations:

- Every measured component carries full per-component provenance (§2,
  finding 6): `written_by.kind = "measurement_pipeline"` (or
  `"engine_config_sync"` for `flags`), `source_receipt_keys`, `pipeline` +
  `pipeline_version`, `measured_at`.
- The `provenance_completeness=legacy` escape (`source_kind=migration`) is
  **migration-only** — it may never appear on a v2+ mint of a re-measured
  component.
- A component the mint does **not** re-measure is carried forward
  **explicitly**, never silently: its provenance block adds
  `carried_forward_from: v{n}` naming the sealed source version whose
  payload (and content hash) it reuses, alongside that source's original
  provenance. The §3 writer-kind rules are unchanged. An unchanged hash
  with no carry marker is a mint error.

### 7.7 Ratified decisions (Costco v2 pilot)

Owner-ratified at the W-A review, recorded here so the pilot's inputs are
part of the contract:

1. **Curated catalog path is DROPPED.** `catalog_snapshot` is populated by
   the observed miner only (labeled receipts, y-band name↔price matching,
   receipt provenance per item); no hand-curated item list ships.
2. **Discarded `_comment` leaves are preserved as PROPOSED records.** The
   v1 migration classified profile `_comment` leaves (including the
   original self-checkout curator note) as discarded-with-reason; each such
   note is imported as a `MERCHANT_TRUTH_PROPOSAL` with git-sha provenance
   and a zero-text-loss check, so no curator knowledge is lost.
3. **Variant-clustering corpus = SCAN receipts.** Clustering for §7.2 runs
   over the 29 Costco SCAN receipts; the 8 PHOTO receipts are **excluded**,
   and the exclusion (count + image type) is recorded alongside the
   clustering output so the corpus is reproducible.
