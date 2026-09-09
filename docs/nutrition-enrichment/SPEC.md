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

First useful milestone, as explicitly narrowed by the owner on 2026-09-08:
a private, runnable lunch calculation for the three named products using
verified product labels, explicit purchase/portion overrides, and exact
portion math. No automatic package-of-one rule. Consuming a portion can be
calculated without building a consumption log. Report assumptions alongside
results; do not turn an inferred carton count or butter amount into evidence.

Before any stream integration, reproduce and fix the devil's advocate
compatibility/freshness findings and compare simpler persistence alternatives
against the same failure cases. Recommend the smallest passing design.
The prior lease/generation prototype is superseded by that comparison.

Broader catalog, backfill, and public UI work are **deferred by the owner**.
They are not prerequisites for lunch and must not resume automatically when
the lunch calculator works. The previously written "resolved v1 decisions"
were proposed defaults, not five decisions confirmed by the owner:

- Restaurant scope: proposed exclusion for a future grocery pilot; undecided.
- Model: undecided; no LLM is needed for the three manually verified products.
- Trader Joe's: read observed product pages/labels for lunch. Automated bulk
  fetching and its terms-of-use judgment remain undecided and deferred.
- OFF/public licensing: undecided and irrelevant to this private result.
- Golden fixture origin: undecided. Use synthetic failure cases and the
  owner's supplied lunch inputs now; no production export is required.

Only portion size, label applicability, package count/weight and purchase
price materially affect this result. Keep their provenance visible and allow
explicit scenario overrides without pretending the owner confirmed them.

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

Deferred, not in this milestone: alias changes will create stream fan-out
work, and a correction will count as propagated only when all affected
active observations, including negative/pending ones, reflect the new
revision, with pending propagation reported explicitly. No code implements
this yet and there is no alias-to-receipt index; finding affected receipts
today would be a table scan. The minimal reverse pointer is card D work.

### Receipt nutrition: bounded atomic document

The current comparison is documented in [RECEIPT_PUBLICATION.md](RECEIPT_PUBLICATION.md).
For bounded private receipt persistence, use one item at
`IMAGE#{image_id}` / `RECEIPT#{receipt_id:05d}#NUTRITION_SUMMARY`.
It holds rows and summary together, the source fingerprint, explicit context
hash (facts/aliases/quantity overrides/calculator version), and an opaque
revision for compare-and-swap. There are no staging rows, publication leases,
reverse indexes, or cleanup jobs in this milestone. Validate size before I/O.

Read the current parent identity and line-item set, not stream counters, to
check freshness. A different parent or source set means stale. A missing
parent means absent. A changed context means stale even if receipt text is
unchanged. Validate source again before writing; condition the write on the
parent timestamp and expected document revision. On a race after the source
check, read-time validation must detect the mismatch. A completed parent-only
delete/recreate cannot inherit a fresh snapshot when its identity differs.

Existing line-item writers have no atomic source-generation marker. Two
consistent observations detect observed churn, but cannot prove a multi-item
source was never transiently partial between observations. Do not claim a
linearizable source snapshot or enable automatic stream publication on that
basis. The lunch calculation takes explicit local inputs and has no such
source dependency. Stream design stays deferred.

Resegmentation treats both prototype nutrition TYPEs as derived, ignores
their churn in plan fingerprints, and sweeps them when deleting the source.
It never copies old nutrition into newly segmented receipts.

## 4. Deferred proposals: catalog and matching

The next proposed acquisition card is **M: one versioned Target adapter** in
AGENT_PLAN.md, informed by research-merchant-lookup-methods.md. Record method
ID/revision with source evidence, and validate identity, package variant,
serving units and prepared/as-sold basis. Local script probes are not Lambda
access proof. RedSky remains undocumented. Self-ranking registries, browser
workers, weekly canaries and automatic rediscovery are deferred until a
demonstrated need. Finish/evaluate the current lunch milestone first.

The broader catalog/matching proposals below are later possibilities; they
do not override the owner's one-adapter starting scope.

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

## 5. Deferred proposals: events, concurrency, and deletion

Do not implement stream integration in the lunch milestone. Revisit queue
ordering, source-generation visibility and bounded repair using the measured
comparison, not the superseded lease design. FIFO serializes normal delivery
for a receipt group but does not make several DynamoDB rows an atomic read.
A summary row count alone cannot detect a mixed same-count rewrite.

## 6. Current private surface and future proposals

The first surface is a local CLI taking a validated input JSON document and
returning a readable portion calculation plus machine-readable results.
Inputs keep receipt prices, purchase quantities, portions, label references
and assumptions separate. Actual lunch inputs/results stay outside git and
public assets. There is no external send, deployment or model call.

Both MCP servers, larger merchant catalogs, alias worklists, stream/queue
fan-out, backfill and public projections are deferred. Earlier thresholds
(150 lines, 0.98 precision over 100 acceptances, a 20–30-product pilot) are
proposed future evaluation designs, not owner decisions or lunch gates.

## 7. Current gates

1. Reproduce the review's compatibility and freshness failures offline.
2. Run actual resegmentation plan/apply with nutrition presence and churn.
3. Compare parent recreation, source rewrites, mixed reads, interrupted
   writes, stale workers, correction context and retry failures.
4. Verify label facts and applicability; expose missing/current-carton
   evidence instead of filling it from an LLM or generic product silently.
5. Run explicit portion overrides through exact arithmetic. Calculate cost
   directly from unrounded receipt price ratios; missing nutrients remain
   missing. Report complete totals separately from available subtotals.
6. Produce a private working lunch answer and a reproducible command, with
   teaspoon/tablespoon sensitivity and carton-count assumptions visible.
7. Independent full-diff review, then commit/push the tested milestone.

## 8. Done and deferred work

The owner's latest instruction narrows the active goal to the lunch result,
review repair and measured persistence recommendation. Future expansion
requires a new scope decision; do not treat a working lunch as authorization
for catalog/backfill/public UI. Dev deployment and merges retain AGENTS.md's
explicit authorization requirements.

Record source URLs/observation dates, commands/counts, actual failures and
fixes, and limits in EVALUATION.md. Local correctness, live provider quality,
MCP deployment and public release are separate evidence levels.

## References checked during revision

- [USDA bases/missing data/versions](https://fdc.nal.usda.gov/GBFPD_Documentation/)
- [USDA downloads](https://fdc.nal.usda.gov/download-datasets/)
- [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [Lambda SQS settings](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-configure.html)
- [OFF licensing](https://openfoodfacts.github.io/documentation/docs/Product-Opener/api/tutorials/license-be-on-the-legal-side/)
- [ODbL definitions](https://opendatacommons.org/licenses/odbl/1-0/)
