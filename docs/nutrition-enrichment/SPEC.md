# Spec: Nutrition enrichment for receipt purchases

Status: revised implementation contract, 2026-09-08.
Branch: `codex/nutrition-enrichment`, based on main `dd737e154`.
Companions: [AGENT_PLAN.md](AGENT_PLAN.md), [EVALUATION.md](EVALUATION.md),
[research-data-model.md](research-data-model.md), [research-data-sources.md](research-data-sources.md).
Research counts and browser probes are historical observations from
`64058f299`, not independently repeated evaluation results. This contract
supersedes the companions' architecture and estimates.

## 1. Outcome and decisions

Attach source-backed food identity and nutrition to purchases. Calculate
serving cost and purchased nutrients only when quantity, package size, and
nutrient basis support the calculation. Show uncertainty and propagate
corrections to past and future purchases.

First useful milestone: a private MCP pilot over 20–30 recurring products,
including Tater Bites, large eggs, and Costco Irish butter. The earlier lunch
table is a case to verify, not fixture truth. Confirm the exact package,
label, and units before accepting its arithmetic. Buying a dozen eggs and
eating three eggs are different operations.

Then expand the harness, integrate the stream, evaluate on authorized dev,
and add a reviewed public visualization. Resolved v1 decisions:

- Exclude restaurants; labelled grocery prepared foods remain eligible.
  Consumption logging and dietary advice are separate work.
- USDA FoodData Central (FDC) is primary. Optional Open Food Facts (OFF)
  enrichment stays private initially.
- Start Trader Joe's with observed products and complete verified label
  evidence. Bulk browser fetching is optional future work, not a dependency
  or an action authorized by this plan.
- Select a configurable model using real held-out evaluation. LLMs choose
  candidates and never generate nutrient facts. Existing credentials do not
  determine the model. Automatic acceptance stays off until its gate passes.
- Use sanitized existing fixtures/dev examples and independently checked
  product evidence. Do not query prod for evaluation.

Historical constraints: preserve deterministic Python/Swift decoders and
their parity fixtures; external products do not belong in the observed
MerchantCatalogItem partition. Reconciliation verifies arithmetic, not
identity or package size. All DynamoDB operations live in receipt_dynamo,
which imports no siblings. Use existing GSIs only, with no new vector index.

Root/nested AGENTS.md apply. Dev deployment/live tests need an explicit user
request; merges and public releases need the required authorization. Finish
local code, tests, reviews, and the concrete rollout packet before requesting
that final authorization.

## 2. Evidence and dimensional math

ReceiptLineItem retains quantity and unit price but discards recognized unit
of measure. Never assume an arbitrary quantity counts packages or fill an
unknown quantity with one. A narrow adapter may recover an explicit unit
from raw_text, validate agreement with stored numeric fields, and record the
exact evidence. It must not repair decoder names, prices, or reconciliation.
Missing/conflicting evidence leaves dependent calculations unknown.

Every line has three independent outcomes:

1. Identity: MATCHED, ESTIMATED, PENDING, NO_MATCH, NOT_FOOD, or SKIPPED, with
   reason and candidate evidence.
2. Quantity: KNOWN, UNKNOWN, or CONFLICT, with provenance and purchased
   count/mass/volume when supported.
3. Nutrition: supplied nutrient values and their bases/sources; absent
   nutrients remain unknown. A matched product does not imply a complete panel.

Canonical amount units: g, ml, each. Nutrient units: g, mg, ug, kcal.
Preserve per-100-g, per-100-ml, per-serving, and per-item bases. Convert mass
to volume only with explicit product-specific density and provenance.
Preserve household serving text, edible-portion assumptions, and
raw/cooked/prepared state. Distinguish oz from fl oz. Reject nonfinite,
negative, zero-denominator, and incompatible physical quantities.
A reported nutrient zero differs from a missing value.

Use Decimal from strings. Retain unrounded calculations and declare money
rounding at the output boundary. Do not calculate from conflicting package
and serving bases.

| Supported evidence | Calculation |
|---|---|
| q packages, package amount n, serving amount s in the same basis | amount q*n; servings q*n/s; cost/serving extended_price/servings |
| Explicit measured mass/volume | convert within dimension; servings amount/s; cost/serving extended_price/servings |
| q each and evidenced grams/item g | mass q*g; serving count follows the declared serving; never discard q |
| Explicit single-package count or confirmed purchase override | count one, retaining its evidence |
| Unknown count, size, density, or serving | dependent fields null; independent product facts still visible |

Purchased nutrients = basis_value * purchased_amount / basis_amount only
with compatible evidence. Generic large-egg nutrition is ESTIMATED, even if
the dozen count is proven. Missing servings/container may be derived from
compatible net/serving quantities and marked derived. Preserve supplied
servings/container as supplied.

Discounts, fees, refunds, deposits, and damaged OCR have distinct reasons.
Initially report product-line spend excluding separate adjustments and show
unallocated adjustments separately; do not call this receipt net spend.
Exclude returns from nutrition totals with an explicit reason until a return
allocation policy exists. Track nutrient completeness per nutrient.

## 3. Storage and dependency contract

receipt_nutrition owns pure models/adapters, retrieval, matching, costing,
and orchestration. receipt_dynamo owns entities, accessors, retries, batches,
and conditional publication.

### FoodProduct: immutable product revision

PK=FOOD_PRODUCT#{product_id}, SK=REV#{content_hash}, TYPE=FOOD_PRODUCT.
Stable product identity is separate from source IDs and revision. Store
brand/name/package variant, verified GTIN if available, net/serving amount
and basis, nutrient facts, generic/exact classification, and source evidence
with dates, acquisition method, quality, and public licensing policy.

Aliases pin an explicit revision. New catalogs do not silently rewrite old
facts. Historical rematching requires an explicit correction/revalidation.
Cross-source equivalence needs evidence. Raw payloads live in a hashed
artifact, not arbitrary verbatim DynamoDB fields. Provide a validated manual
product import; manual facts need provenance and are not publishable by default.

### ProductAlias: scoped conditional resolution

PK=PRODUCT_ALIAS#{merchant_slug}, SK=TEXT#{normalized_text} or
ITEM#{retailer_item_number}, TYPE=PRODUCT_ALIAS.
Use existing text normalization and the merchant fleet alias map when
available, then the existing normalization fallback. Case-only merchant
variants already share the line-item slug. Retailer item numbers are not
GTINs; suffix matching cannot constitute identifier evidence.

Fields: revision, status, product ID/revision, method, candidates/evidence,
size/variant applicability, model/prompt/catalog versions, confirmed_by_user,
changed_at, expires_at. Confirm with an expected revision. A stale model
result cannot overwrite a user decision. Confirmations have no TTL but keep
their applicability limits and can be explicitly superseded.
Ambiguous size variants remain pending. Check expiry in application code;
Expired automatic/negative decisions retry using the existing revision.
Retain alias records without the table TTL attribute; TTL deletion could reset
revision numbering and let delayed work match a recreated alias. Key components
are percent-escaped so merchant/text delimiters cannot collide.

Alias changes create stream fan-out work. A correction is propagated only
when all affected active observations, including negative/pending ones,
reflect the new revision. Report pending propagation explicitly.

### ReceiptLineNutrition: immutable generation rows

PK=IMAGE#{image_id},
SK=RECEIPT#{receipt_id:05d}#NUTRITION#{generation}#{item_index:05d},
TYPE=RECEIPT_LINE_NUTRITION.
Include line fingerprint, alias key/revision, product ID/revision,
catalog/matcher/costing versions, all three outcomes, quantity/calculation
evidence, prices, and nullable derived nutrients and amounts.

GSI1 indexes matched product observations. GSI2 indexes alias observations
for every status. These include staged/old rows: reverse lookup accessors
must check parent existence, join each receipt's active summary generation,
and discard inactive rows and observations whose parent no longer exists.
Never total raw GSI hits. Every source line retains a status/reason.

### ReceiptNutritionSummary: control and publication manifest

PK=IMAGE#{image_id}, SK=RECEIPT#{receipt_id:05d}#NUTRITION_SUMMARY,
TYPE=RECEIPT_NUTRITION_SUMMARY.
Fields: requested/committed revision, lease owner/deadline, active generation,
input fingerprint, dependencies, outcome counts, product-line and calculable
spend, adjustments, nutrient totals/completeness, content version, enriched_at.

Stage generation rows, verify their count/hash, then atomically publish this
manifest. Read only its generation; incomplete staging cannot expose partial
committed results. Cleanup acquires the same receipt lease as publication:
it cannot run while a publisher can still commit its staged generation, and
never removes the active generation. Orphan cleanup verifies the parent is
absent. Readers verify row count/hash and re-read the manifest; if a newer
publication/cleanup raced their read, retry instead of returning partial data.
Expose committed/stale/pending state. The dedicated get_receipt_nutrition
accessor enforces generation/freshness semantics; generic get_receipt_details
must not inadvertently include every old generation. GSI4 is optional.
delete_receipt_items is the existing child-prefix sweep; singular
delete_receipt does not perform that sweep.

## 4. Catalog and matching

Order: gate/reason → applicable confirmed alias → unexpired automatic alias →
verified exact identifier mapping → lexical candidates → optional model →
pending review. No step fills unsupported nutrition/size from model output.
A generic source stays an estimate even if text matches exactly.

Start with deterministic SQLite and character n-grams. Include national
brands (including Kerrygold), generic foods, package variants, and negative
candidates. Merchant is a ranking feature, not a filter excluding correct
national brands when one weak store-brand candidate passes a threshold.

Use one adapter/import path for full and miniature catalogs. Pin source URLs,
release dates, hashes, source/basis fields, and format version in a manifest.
April 2026 is the reviewed current FDC download. Bulk artifacts stay outside
git. Do not inject missing truth products after retrieval fails.

Optional OFF import preserves its licensing metadata. TJ import must have
serving size/unit and actual label basis; the demonstrated GraphQL
macronutrient block alone is insufficient. Source outages preserve pinned
facts and leave new cases pending. Bulk scraping/bot-defense workarounds are
not prerequisites.

Models use environment configuration, SecretStr, timeouts, bounded retries,
candidate-ID-only structured responses, prompt version, and measured token
cost. Treat receipt/catalog text as untrusted. Reject nonexistent candidates
and incompatible variants. Self-reported confidence is not probability.
Keep automatic LLM acceptance disabled until the held-out gate passes.

## 5. Events, concurrency, and deletion

Extend the existing receipt_dynamo_stream fan-out without removing its two
existing target queues. Add parser/model, relevant-field allowlist, INSERT
handling, extractor routing, queue enum, and publisher together.
Relevant fields include name, price, quantity, unit_price, raw_text,
name_quality, is_discount, merchant_name, reconciliation_status, extracted_at.
Line INSERT/MODIFY/REMOVE request receipt refresh. Receipt-parent REMOVE
requests terminal cleanup and cache invalidation, including the existing
parent-only MCP deletion path. Alias changes request fan-out.
Nutrition/control and immutable product rows cannot create loops.

Consumer contract:

1. Atomically mark dirty (increment requested revision) and acquire a bounded
   lease with a unique owner token. Busy work retries rather than being
   acknowledged complete. Batch-local deduplication is only an optimization.
   Alias fan-out is paginated/checkpointed and includes negative observations.
2. Strongly read parent and current line items. A missing parent forbids
   publication. Capture input fingerprint and requested revision.
3. Resolve dependencies, calculate, and re-read source before publication.
   Changed input retries. Stage immutable rows and verify count/hash.
   Transactionally publish only if parent exists, lease owner/deadline and
   requested revision still match. Expired/superseded workers cannot publish.
4. Zero-line receipts publish an empty generation retiring previous content.
   In-flight work cannot recreate a deleted or merged-away receipt. Parent
   deletion is terminal success only after child cleanup and a cache-refresh
   request have been recorded/enqueued. Request cache refresh even when merge
   cleanup already removed the manifest; use the event's receipt keys.
5. Identical delivery changes zero business content; count coordination
   writes separately. Content hashes exclude processing clocks/hit counters.
   Partial batch failures retain original SQS message IDs.

Upstream delete/insert writes are not atomic. Queue delay cannot prove final
source state; the guarantee is convergence after writes quiesce, with
freshness exposed. Reads compare input/dependencies before calling committed
content current. A bounded periodic repair detects changed inputs/aliases
and stranded work, including interrupted fan-out and delete-only changes.
Periodic full cache regeneration replaces outputs from surviving parents and
current manifests, removing contributions whose manifest/parent disappeared
even if a deletion notification was lost. Do not rely solely on successful
nutrition publication as the cache trigger.

Defaults: delivery delay 60s, batch window ≤60s, function timeout 120s,
visibility ≥780s (6*120+60), DLQ after ≥5 attempts, ReportBatchItemFailures,
bounded concurrency, error/DLQ/staleness metrics. Tune catalog size, cold
load, memory, and runtime against the actual ARM64 artifact.

## 6. Private and public surfaces

Implement shared business logic with thin adapters in both MCP server copies:
get_receipt_nutrition, search_food_products, validated manual import,
list_nutrition_worklist, confirm/reject_product_match with expected revision,
and nutrition_spend. Reads include evidence/freshness; mutations default to
dev and refuse prod writes. Totals distinguish estimates and incompleteness.

Current main includes the line-item decoder visualization (#1391). Extend
its explanation after private correctness. Static export can use reviewed
static JSON or API Gateway reads; a cache is an aggregation choice, not a
requirement imposed by Next.js itself.

Public projection happens server-side before serialization: permitted
sources only, selected examples, no private notes or excluded identifiers,
and no OFF-derived facts or aggregates in v1. Recompute public totals from
permitted rows. Browser hiding is not publication control. Test full response
bytes, nested evidence, and monthly totals. Manual provenance requires its
own explicit public permission.

Dynamic caches use the cache bucket. Trigger generation after successful
publication and provide a bounded repair schedule, idempotent generation,
cache version/age, and invalidation. Corrections refresh receipt and monthly
outputs. Frontend needs before/after screenshots and owner review before merge.

## 7. Gates

Record each milestone in EVALUATION.md. Run independent codex exec diff
review before every commit/push. Distinguish fake/offline, source-backed,
live-provider, dev-runtime, and browser results; unavailable means pending.

| Gate | Acceptance |
|---|---|
| Pilot | 20–30 recurring products; exact/generic separated; motivating examples verified or explicitly pending |
| Quantity/costing | ≥30 independent mass/volume/count/multipack/unknown/conflict/adjustment cases; cent-exact supported costs; no invented amounts |
| Expanded fixture | ≥150 lines, ≥6 merchants, ≥20 adjustment/non-food cases; provenance and product-family grouping |
| Real imported retrieval | recall@10 ≥0.85; counts and exact-size recall separately |
| Real LLM automatic acceptance | ≥0.98 observed precision on ≥100 accepted held-out cases; per-merchant/variant counts; stays off until passed |
| Abstention | absent size/unit/nutrients and out-of-catalog cases cannot become exact facts |
| Coverage | exact identities, estimates and calculable spend separate; target ≥0.70 calculable eligible food spend with fixed denominator |
| Corrections | all affected active purchases and receipt/monthly outputs update; stale automatic decisions cannot undo confirmations |
| Concurrent/deletion behavior | duplicate/reordered events, lease expiry, staging crash, cleanup between verification/publication, delete-only, zero lines, parent-only deletion and merge recover correctly; deleted receipts disappear from caches |
| Idempotency | second identical pass changes zero business rows; coordination separately counted |
| Authorized dev | upload/refine/correct/delete → current reads/cache within five minutes after source quiescence |
| Public/UI | excluded facts absent from bytes and totals; zero/partial/full/estimated/unknown states; lint/types/tests/screenshots/owner review |

Split by product/alias family, not repeated receipt lines. Keep tuning
examples outside held-out evaluation. Synthetic/FakeLLM tests exercise
contracts only. No source or model labels its own predictions as truth.
Report numerator/denominator and unknowns, not just percentages.

## 8. Cost, rollout, and done

2,500 calls * 1,500 input tokens = 3.75 million input tokens, $3.75 at standard
Haiku 4.5 input pricing before output/retries/tooling. Deduplication/batching
can reduce it but must be measured. Configure per-run call/cost caps and
dry-run estimates. Memory/startup/monthly budget claims require measurements.

Backfill is dev-only, dry-run by default, limit-required on apply, resumable,
and version-aware; enqueue through the normal consumer without rewriting
decoder rows. New packages enter package CI, repository installs, Lambda
packaging/import gates, and changed-source hashing.

After explicit dev authorization, verify AWS account 681647709217, then
preview/up from infra with fully qualified tnorlund/portfolio/dev. Refuse
unrelated deletes/replacements and never interrupt a shared update.
Prepare draft PRs and evidence; merge/public release need authorization.

Done means pilot, expanded real evaluation, persistence/stream/correction
tests, authorized dev end-to-end, and reviewed public surface pass. Local
implementation, PR, dev deployment, and production release are separate
ledger entries. Missing truth, credentials, authorization, or visual review
stay pending rather than being recorded as success.

## References checked during revision

- [USDA bases/missing data/versions](https://fdc.nal.usda.gov/GBFPD_Documentation/)
- [USDA downloads](https://fdc.nal.usda.gov/download-datasets/)
- [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [Lambda SQS settings](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-configure.html)
- [OFF licensing](https://openfoodfacts.github.io/documentation/docs/Product-Opener/api/tutorials/license-be-on-the-legal-side/)
- [ODbL definitions](https://opendatacommons.org/licenses/odbl/1-0/)
