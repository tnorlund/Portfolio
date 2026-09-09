# Nutrition enrichment for receipt line items: data-source research

> Revision note (2026-09-08): this is the original source-research snapshot,
> not an implementation contract or independently repeated coverage report.
> Follow [SPEC.md](SPEC.md). USDA now lists an April 2026 download; the Dec
> 2025 release below is historical. At standard Haiku 4.5 pricing, 2,500 calls
> of 1,500 input tokens cost $3.75 for input alone, before output/retries.
> Preserve mass/volume/count and serving bases; missing nutrients are unknown.
> The TJ query below lacks serving-size fields and cannot alone substantiate
> the proposed calculations. Start with complete observed-product label
> records; bulk fetching is not required or authorized by this document.
> OFF/public licensing needs an explicit source policy before serialization
> and aggregation, not a browser filter. Manual facts also need provenance.
> ODbL distinguishes derivative databases, collective databases, and produced
> works; the original shorthand below is not a definitive legal conclusion.

Date: 2026-09-08. Sources are primary docs plus live probes run today against the USDA FoodData Central (FDC) API and the Open Food Facts (OFF) APIs. Anything not verified is flagged.

Use case: single user, ~800 paper grocery receipts, ~45K OCR'd line items, no UPCs on receipts, store-brand heavy (Trader Joe's, Sprouts, Costco/Kirkland, Smith's/Kroger, Target, Dollar Tree, Wild Fork).

---

## 1. Nutrition data APIs

### 1.1 Comparison table

| Source | Cost | Auth | Rate limits | License / attribution | Store-brand coverage (probed) | Bulk download |
|---|---|---|---|---|---|---|
| USDA FoodData Central, Branded Foods | Free | data.gov API key | 1,000 req/hr/IP default. DEMO_KEY documented as 30/hr and 50/day; in practice the response header showed `x-ratelimit-limit: 10` and I got 429 with `retry-after: 11706` after ~15 calls | Public domain, CC0 1.0; cite "FoodData Central" | ~2.0M branded records total (1,999,950 of 2,013,644 FDC entries). By `brandOwner` filter: Kirkland Signature 295, Good & Gather 1,332, Trader Joe's only 195 (TJ products are filed under supplier brand owners such as "Starion, LLC", "Mood Mania, Inc." with brandName TRADER JOE'S). Kroger / Simple Truth / Wild Fork brandOwner queries returned nothing | Yes. Dec 2025 Branded release: CSV 427 MB zipped / 2.9 GB, JSON 195 MB / 3.1 GB. Download refreshed ~every 6 months (Apr/Oct), API monthly |
| Open Food Facts | Free | None; custom User-Agent `AppName/Version (email)` required | 15 req/min/IP for product reads, 10 req/min/IP for search; IP ban if exceeded; search-as-you-type explicitly discouraged | ODbL (database) + DbCL (contents); derived databases must be shared under ODbL; images CC-BY-SA; OFF asks to be emailed at reuse@openfoodfacts.org | search-a-licious brand counts (world): Kroger 5,715; Simple Truth 1,731; Market Pantry 1,065; Kirkland Signature 1,044; Good & Gather 803; Sprouts Farmers Market 709; Wild Fork 166; Trader Joe's split across three tags: "trader-joe's" 4,305 + "trader-joes" 477 + "trader-joe-s" 28 (US brands facet page shows 3,392). OFF v2 search with brands_tags=trader-joes returned 435 | Yes. MongoDB dump + JSONL nightly with 14-day deltas; CSV ~0.9 GB zip / 9 GB; Parquet on Hugging Face `openfoodfacts/product-database` food.parquet 5.74 GB, 4.78M rows |
| Nutritionix (Syndigo) | Contradictory. A March 2026 post says free tier = 200 calls/day with attribution; a July 2026 post and Nutritionix's own copy say no public free tier, trial by request only. Paid: custom enterprise; third parties cite ~$50/mo hobby (~10K/day), $299/mo starter, $500 to $2,000+/mo production | `x-app-id` + `x-app-key` headers | Per plan | Attribution required in any UI, visible without scrolling. Caching only permitted to record a user's own historical food-log transaction; no shared cache | 600K+ foods; ~93% US branded coverage claimed by third party | None; terms forbid it |
| Edamam Food Database | Developer: $0, 10 calls/min, 10K calls/month, non-commercial. Enterprise Basic $14/mo (100K calls, 50/min), Core $69/mo (750K), Plus $299/mo (5M, 300/min) | app_id + app_key | See tiers | Logo + link attribution mandatory, breach = suspension. "All plans allow only human, end user driven requests" and prohibit "automated programatic requests with the goal to collect, scrape or save data" | ~900K foods, 790K UPCs, 130K restaurant items | No, and storing results is prohibited |
| FatSecret Platform | Basic: free self-signup, 5,000 calls/day, US-only data, barcode included, no NLP/image. Premier Free: unlimited, verification required (startups, nonprofits, students). Premier: quote, 58+ countries, no attribution | OAuth 2 client credentials (3-legged only for user data) | 5,000/day on Basic | Attribution required on Basic and Premier Free | 2.3M foods incl. generic, branded, supermarket, restaurant; per-brand counts not published | No |
| Chomp | Limited: $0 base + $0.01/request, 30 req/min, barcode lookup only, 900K branded. Standard: $25/mo + $0.001/req, 100/min, name search, 1.2M foods. Premium $299/mo | API key | 30/min (Limited), 100/min (Standard) | Attribution required (Limited), requested (Standard); caching 24 h on Standard, indefinite on Premium; no reselling as a competing DB | 900K to 1.2M branded | No |
| Spoonacular | Free 50 points/day, 1 req/s, backlink required. Cook $29/mo (1,500 pts/day, 5 rps), Culinarian $79 (4,500), Chef $149 (10,000) | API key | Grocery product search = 1 point + 0.01/result, +1 point per product when `addProductInformation=true` | Backlink on free tier | Recipe-first; grocery product DB not store-brand focused | No |

### 1.2 Design-relevant details

- FDC branded nutrient values are normalized to per 100 g or per 100 ml, calculated from the label's per-serving values. The API `food/{fdcId}` response also carries a `labelNutrients` block with per-serving label values: fat, saturatedFat, transFat, cholesterol, sodium, carbohydrates, fiber, sugars, protein, calcium, iron, potassium, calories.
- FDC `foods/search` params: `query`, `dataType` (array, use `Branded`), `pageSize`, `pageNumber`, `sortBy`, `sortOrder`, `brandOwner`. Search result foods include `foodNutrients[]` with `nutrientId`, `nutrientName`, `nutrientNumber`, `unitName`, `value`, plus `brandOwner`, `brandName`, `gtinUpc`, `servingSize`, `servingSizeUnit`, `householdServingFullText`, `packageWeight`, `publishedDate`.
- FDC search is fuzzy and noisy. "trader joe's tater bites" returned 6,313 hits led by TJ cornichons and Harris Teeter tater bites; "kirkland irish butter" returned 64,466 hits led by Kerrygold. `brandOwner` is a text match ("sprouts farmers market" matched 25,098 rows, "market pantry" 24,801). Do not use the search endpoint as the matcher. Load the CSV and do retrieval locally.
- FDC duplicate GTINs signify product updates; distinguish by `publication_date` in the food table and keep the latest. Discontinued products are retained with `discontinued_date`.
- FDC missing nutrients mean "not supplied by provider", not zero. "Not a significant source of" statements can appear instead of numbers.
- OFF full-text search is not in the v2/v3 product API; use search-a-licious at `https://search.openfoodfacts.org/search?q=...&page_size=&fields=` (Lucene-like syntax, e.g. `q=brands:"trader-joes"`). Brand tags are inconsistent (three TJ tags), so union them.
- Edamam's terms forbid exactly this use (programmatic collection and saving). Exclude it.
- OFF is share-alike. A privately held enrichment table is fine; publishing a derived nutrition table on the public site would have to be ODbL.

Sources:
- FDC API guide: http://fdc.nal.usda.gov/api-guide/
- FDC downloads: http://fdc.nal.usda.gov/download-datasets/
- FDC OpenAPI spec: https://fdc.nal.usda.gov/api-spec/fdc_api.html
- FDC GBFPD documentation: https://fdc.nal.usda.gov/GBFPD_Documentation/
- FDC field descriptions PDF: https://fdc.nal.usda.gov/docs/Download_Field_Descriptions_Oct2020.pdf
- FDC update log: http://fdc.nal.usda.gov/log/
- FDC 2.0M count (third party, Aug 2026 refresh): https://getfoodfacts.com/
- OFF API intro (rate limits, UA, license): https://openfoodfacts.github.io/openfoodfacts-server/api/
- OFF API conditions: https://support.openfoodfacts.org/help/en-gb/12-api-data-reuse/94-are-there-conditions-to-use-the-api
- OFF data exports: https://world.openfoodfacts.org/data
- OFF Parquet on Hugging Face: https://huggingface.co/datasets/openfoodfacts/product-database
- OFF US brands facet: https://us.openfoodfacts.org/brands
- OFF search-a-licious docs: https://search.openfoodfacts.org/docs
- Nutritionix getting started (attribution): https://docx.syndigo.com/developers/docs/getting-started-1
- Nutritionix FAQ (caching): https://docx.syndigo.com/developers/docs/faqs
- Nutritionix nutrient IDs: https://docx.syndigo.com/developers/docs/list-of-all-nutrients-and-nutrient-ids-from-api
- Nutritionix pricing commentary (Mar 2026): https://selfhostednutrition.org/api/nutritionix-api-when-to-use/
- Nutritionix pricing commentary (Jul 2026): https://calorieapi.com/blog/nutritionix-api-pricing
- Nutritionix trial request: https://www.nutritionix.com/request-api-trial
- Edamam Food Database API: https://developer.edamam.com/food-database-api
- FatSecret editions: https://platform.fatsecret.com/api-editions
- FatSecret platform overview: https://platform.fatsecret.com/platform-api
- Chomp: https://chompthis.com/api/
- Spoonacular pricing: https://spoonacular.com/food-api/pricing
- Spoonacular grocery search cost: https://www.worldindata.com/api/spoonacular-search-grocery-products-api/

---

## 2. Retailer product data

### 2.1 Trader Joe's

- No official feed. The Terms of Use prohibit copying any portion of the site and interfering with servers; the page itself returns 403 to scripted fetches (summary from search snippet: https://www.traderjoes.com/home/terms-of-use).
- Unofficial GraphQL at `https://www.traderjoes.com/api/graphql`, no auth. Bot defense returns 403 to curl/scripts (confirmed by team lead today) but browser contexts, Apify scrapers, and several MCP servers work.
- SKUs are 6-digit strings and appear in product URLs: `/home/products/pdp/tater-bites-with-cheese-and-chives-084621`, `/spicy-spuds-082237`, `/two-potato-hash-082488`. Receipts do not print SKUs, so TJ matching is name-only.
- Exact product-detail query, pulled from `src/index.ts` of the MIT-licensed `markswendsen-code/mcp-traderjoes` repo (it POSTs with only `Content-Type: application/json`):

```graphql
query GetProduct($storeCode: String, $published: String, $sku: String) {
  products(
    filter: { store_code: { eq: $storeCode }, published: { eq: $published }, sku: { eq: $sku } }
    pageSize: 1
  ) {
    items {
      sku
      item_title
      category_hierarchy { id name url_key }
      primary_image
      primary_image_meta { url caption }
      sales_size
      sales_uom_description
      retail_price
      fun_tags
      item_characteristics
      new_product
      fearless_flyer_applicable
      ingredients
      nutrition_facts { calories total_fat saturated_fat trans_fat cholesterol sodium total_carbohydrate dietary_fiber total_sugars protein }
      allergens
      directions
    }
  }
}
```
Variables: `{ "storeCode": "TJ", "published": "1", "sku": "084621" }`.

- Catalog enumeration query (from `cmoog/traderjoes`, Haskell, MIT, runs daily against store code 701):

```graphql
query SearchProducts($pageSize: Int, $currentPage: Int, $storeCode: String, $published: String = "1") {
  products(filter: {store_code: {eq: $storeCode}, published: {eq: $published}}, pageSize: $pageSize, currentPage: $currentPage) {
    items { product_label primary_image published sku url_key name item_description item_title item_characteristics
            sales_size sales_uom_code sales_uom_description country_of_origin availability new_product promotion
            price_range { minimum_price { final_price { currency value } } } retail_price
            created_at first_published_date last_published_date updated_at }
    total_count
    page_info { current_page page_size total_pages }
  }
}
```
The MCP server's search variant adds a `$search: String` variable and uses storeCode "TJ".

- The `nutrition_facts` block lacks vitamin D, calcium, iron, potassium, added sugars and servings per container; the PDP HTML nutrition panel has the full label.
- Scale: TJ stocks ~4,000 items, ~80% private label, so a full SKU cache is one-time small.

Sources:
- https://github.com/markswendsen-code/mcp-traderjoes (MIT; endpoint and queries)
- https://mcpmarket.com/server/trader-joe-s
- https://glama.ai/mcp/servers/markswendsen-code/mcp-traderjoes/tools/get_product_details
- https://github.com/cmoog/traderjoes (daily price tracker, query.graphql, store 701)
- https://github.com/jackgisel/traderjoeapi
- https://github.com/binthroot/trader-joes-api
- https://apify.com/merry_arctic/trader-joes-scraper
- https://www.traderjoes.com/home/products/pdp/tater-bites-with-cheese-and-chives-084621
- https://www.traderjoes.com/home/products/pdp/spicy-spuds-082237
- https://www.traderjoes.com/home/products/pdp/two-potato-hash-082488

### 2.2 Costco

- No public API. costco.com search accepts item numbers, but many warehouse-only grocery items are not listed online, and the site is behind CAPTCHAs / JS rendering (all open-source "scrapers" route through paid proxy APIs).
- Receipt item numbers (e.g. 1172471, 1068080) are stable identifiers; use them as alias keys. Resolve nutrition via FDC (brandName "Kirkland Signature", 295 rows) and OFF (1,044).
- Product URL pattern uses a separate web product id (`.product.4000315948.html`), not the item number.

Sources:
- https://customerservice.costco.com/app/answers/answer_view/a_id/668/~/how-can-i-locate-a-product-on-costco.com
- https://github.com/ScraperHub/costco-scrapers
- https://github.com/ScrapingBee/costco-scraper
- https://docs.unwrangle.com/costco-product-data-api/

### 2.3 Kroger (Smith's)

- Official, free developer program: https://developer.kroger.com/. Register an app, OAuth2 client-credentials token with scope `product.compact`.
- `GET /v1/products?filter.term=&filter.locationId=&filter.brand=&filter.productId=&filter.limit=` and `GET /v1/products/{id}`. Rate limit: Products API 10,000 calls/day (Locations 1,600/day per endpoint, Cart 5,000/day, Identity 5,000/day).
- Response fields: productId, upc, description, brand, categories, images, items[] {itemId, price {regular, promo}, size, soldBy, fulfillment}, itemInformation {depth, height, width}, temperature {indicator, heatSensitive}, aisleLocations[], countryOrigin. No nutrition or ingredient fields in the documented response.
- Use: turn a Smith's line into a UPC, then look the UPC up in FDC by `gtin_upc`.

Sources:
- https://developer.kroger.com/reference/api/product-api-public
- https://developer.kroger.com/documentation/api-products/public/products/overview
- https://github.com/CupOfOwls/kroger-api (rate limits, scopes)
- https://github.com/jtbricker/python-kroger-client/blob/master/docs/api_responses/products.json (sample response, no nutrition)

### 2.4 Sprouts

- shop.sprouts.com runs on Instacart Storefront Pro. Product pages show nutrition and ingredients (Instacart's Universal Catalog obtains UPC-level nutrition from third parties).
- Instacart Developer Platform exposes recipe page, shopping list, and retailer endpoints, not a public catalog search returning nutrition. No open-source Sprouts scrapers found, only commercial ones (Bright Data, etc.).
- Rely on FDC (brandName "Sprouts") and OFF (709 products).

Sources:
- https://www.grocerydive.com/news/kroger-sprouts-instacart-artificial-intelligence-grocery/804637/
- https://docs.instacart.com/developer_platform_api/
- https://brightdata.com/products/web-scraper/sprouts-farmers-market

### 2.5 Target

- RedSky is a public-but-unofficial JSON API: `https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1?key=<static web key>&tcin=&store_id=&pricing_store_id=&zip=&state=&latitude=&longitude=`; `pricing_store_id` must equal `store_id`. Legacy `/v2/pdp/tcin/...` returns 410. Protected by HUMAN (PerimeterX) TLS fingerprinting. A Target employee reportedly said the API is intentionally public.
- Nutrition fields in the pdp_client_v1 response could not be confirmed from public docs.
- Good & Gather (FDC 1,332; OFF 803) and Market Pantry (OFF 1,065) are well covered, so skip RedSky.

Sources:
- https://gist.github.com/LumaDevelopment/f2a34a202fed6ab5a7f3a31282834943
- https://scrapfly.io/blog/posts/how-to-scrape-target-com
- https://www.unwrangle.com/blog/how-to-scrape-target-com/

### 2.6 Wild Fork, Dollar Tree

- No official data. OFF has 166 Wild Fork products. Dollar Tree stocks national brands mostly, so FDC by name works.

---

## 3. Receipt abbreviation to product matching

### 3.1 Literature and industry practice

- KNN string similarity vs LSTM on abbreviated Philippine grocery receipt names: KNN 92.63% average similarity ratio vs LSTM 76.55%, on 17 receipts / 95 unique items. Tiny dataset but the direction (retrieval beats generation) holds. https://link.springer.com/chapter/10.1007/978-3-031-62281-6_26
- Coding retail product names to consumption categories (arXiv 2606.02004, 2026): key-phrase trie + per-category char n-gram logistic regression reached mean F1 0.997; word-order features added nothing; small CNN/LSTM were weakest in the small-data regime; ~66 labels per category sufficed; Dawid-Skene beat majority vote for human-in-the-loop labels. Normalization rule: strip semantically empty tokens, keep quality attributes (fat content, brand, pack size, unit). https://arxiv.org/abs/2606.02004
- Industry (Microblink/BlinkReceipt, Fetch, Ibotta): brand-abbreviation expansion ("GV" to Great Value), per-retailer abbreviation dictionaries, match into a 15M-product catalog keyed by UPC. https://microblink.com/commerce/receipt-ocr/
- US patent 10,943,139 describes applying the retailer's abbreviation rules to catalog names and matching forward, which is cheaper than expanding receipt text. https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10943139
- Embedding retrieval over product catalogs is standard (Instacart two-tower search https://arxiv.org/pdf/2209.05555; sentence-transformers product matching https://github.com/SayamAlt/Shack-Labs-Data-Science-Internship-Tasks). Receipt-specific scoring accounts for receipts dropping letters but keeping key letters.
- LLM normalization with structured JSON output is used to strip brand/quantity/packaging tokens (e.g. "Kraft Sharp Cheddar Cheese (8 oz)" to cheddar_cheese). https://github.com/bchadburn/llm-recsys

### 3.2 Datasets

- CORD (https://github.com/clovaai/cord) and SROIE (https://github.com/zzzDavid/ICDAR-2019-SROIE) are receipt OCR/KIE datasets, not abbreviation-to-product pairs.
- Open Prices (Open Food Facts) is the only public corpus linking receipt images to product barcodes: users upload receipt "proofs", Gemini extracts line items, users confirm on a validation page; Parquet dump on Hugging Face includes proof file paths and images under CC-BY-SA, data ODbL. https://huggingface.co/datasets/openfoodfacts/open-prices , https://blog.openfoodfacts.org/en/news/open-prices-200000-prices-and-beyond , https://github.com/openfoodfacts/open-prices/issues/681
- The 45K labelled lines in this project are larger than anything public.

### 3.3 Recommended pipeline

1. Normalize: uppercase, strip qty/weight/price tokens, expand merchant abbreviations (ORG, LRG, WHI, R- prefix on TJ seasonal items, CF/CAGE FREE), keep attribute tokens.
2. Per-merchant candidate retrieval: char n-gram TF-IDF plus an embedding index over the merchant's catalog names (TJ SKU cache, FDC brandName subsets, OFF brand subsets). Catalogs are a few thousand names each.
3. LLM re-rank of the top 10 with an explicit reject option and a confidence.
4. User confirmation writes `(merchant, normalized_text) -> product_id` to an alias table that short-circuits future lookups. Heavy repeat purchases mean the alias table covers most lines after a few hundred confirmations.

---

## 4. Apps already doing receipt to nutrition

- haul (iOS, free, Premium $7.99/mo or $59.99/yr): photo of receipt, AI reads every line item in ~4 s, matches against USDA + Open Food Facts + FatSecret, populates a pantry with macros and 14 micronutrients, quality grade and NOVA score, remaining quantity. Corrections during receipt review "improve results for everyone who scans that product next"; inline nutrition editing; release notes mention HEB and Whole Foods receipt formats. Lessons: three databases not one; corrections as shared training data; per-merchant format handling; explicit review step. https://gethaul.app/ , https://apps.apple.com/us/app/haul-smart-nutrition/id6760370993
- LeanLens Pro+ lists receipt scanning; no technical detail. https://apps.apple.com/us/app/leanlens-ai-calorie-scanner/id6787576276
- Open Prices is the open-source analog for the receipt-to-barcode step (Gemini extraction plus human validation). https://prices.openfoodfacts.org/
- WonderFood (local-first Android) lists receipts as a feature. https://github.com/jrhizor/awesome-nutrition-tracking
- Mainstream trackers (MacroFactor, Cal AI, etc.) scan barcodes or labels, not receipts.

---

## 5. Per-serving cost math and schema

### 5.1 Conventions in the sources

- FDC branded_food: `serving_size` (numeric) + `serving_size_unit` (g or ml); `household_serving_fulltext` ("2 COOKIES", "1 Tbsp", "9 PIECES"); `package_weight` free text such as "12.35 oz/350 g", "32 oz/2 lbs/907 g", "2 lbs/32 oz" (sometimes null); `gtin_upc`; `brand_owner`; `brand_name`; `branded_food_category`; `data_source` (GDSN or LI); `market_country`; `discontinued_date`. No servings-per-container field: derive `servings = package_grams / serving_size` after parsing `package_weight`, and flag it as derived.
- FDC nutrient table: `id`, `name`, `unit_name`, `nutrient_nbr`, `rank`; `food_nutrient.amount` is per 100 g/ml. `food_portion.gram_weight` gives household measure weights for Foundation/SR Legacy foods (use for weighed produce).
- OFF product fields: `quantity` (label text), `product_quantity` (numeric g/ml), `serving_size` (text), `serving_quantity` (numeric), `nutrition_data_per` (100g or serving), nutriments keyed `<nutrient>_100g` and `<nutrient>_serving` (e.g. `energy-kcal_100g`).
- TJ GraphQL: `sales_size` + `sales_uom_description` give net contents (e.g. 20 oz); PDP panel has servings per container.
- Quantity lines: "2 @ $3.49" means qty 2, unit price 3.49, extended 6.98. Cost per serving = unit_price / servings_per_container. Cost per 100 g = unit_price / package_grams x 100.
- Weighed items: "1.23 lb @ $1.99/lb": grams = lb x 453.592; cost per 100 g = price / grams x 100; nutrition from FDC Foundation / SR Legacy per 100 g (branded data does not cover loose produce); household portions from `food_portion`.

### 5.2 Nutrient identifiers

FDC nutrient `id` 1008 = Energy (KCAL), `nutrient_nbr` 208 (verified in nutrient.csv copies on GitHub, e.g. https://github.com/mrdbourke/nutrify/blob/ba26b82af5d08827f9a6316af094d28349a002bf/data_exploration/data/FoodData_Central_Supporting_Data_csv_2021-04-28/nutrient.csv). The legacy nutrient numbers match Nutritionix `attr_id`s exactly, so keying the label set by legacy number lets any source feed the same schema:

| Nutrient | Legacy nbr / Nutritionix attr_id | Unit |
|---|---|---|
| Calories | 208 | kcal |
| Protein | 203 | g |
| Total fat | 204 | g |
| Carbohydrate | 205 | g |
| Total sugars | 269 | g |
| Added sugars | 539 | g |
| Dietary fiber | 291 | g |
| Sodium | 307 | mg |
| Saturated fat | 606 | g |
| Trans fat | 605 | g |
| Cholesterol | 601 | mg |
| Calcium | 301 | mg |
| Iron | 303 | mg |
| Potassium | 306 | mg |
| Vitamin D | 324 (IU) / 328 (mcg) | IU or mcg |

Other FDC `id` values (from memory, not re-verified because DEMO_KEY rate-limited me; confirm against nutrient.csv in the download): 1003 protein, 1004 fat, 1005 carbohydrate, 1079 fiber, 2000 total sugars, 1235 added sugars, 1093 sodium, 1258 saturated fat, 1257 trans fat, 1253 cholesterol, 1087 calcium, 1089 iron, 1092 potassium, 1110 vitamin D (IU), 1114 vitamin D (mcg).

FDA label mandatory set (21 CFR 101.9): serving size, servings per container, calories, total fat, saturated fat, trans fat, cholesterol, sodium, total carbohydrate, dietary fiber, total sugars, added sugars, protein, vitamin D, calcium, iron, potassium. https://www.fda.gov/food/nutrition-education-resources-materials/nutrition-facts-label

### 5.3 Compact schema proposal

```
product
  product_id            (internal)
  source                fdc | off | tj | kroger_upc | manual
  source_id             fdc_id | barcode | tj_sku | upc
  brand, name
  net_qty, net_unit     g | ml   (parsed from package_weight / product_quantity / sales_size)
  serving_qty, serving_unit, serving_household
  servings_per_container  nullable; derived_flag
  nutrients             map<nutrient_nbr, {per_100: float, per_serving: float, unit: str}>   -- per_100 canonical
  fetched_at, source_version

alias
  merchant, normalized_text -> product_id
  confidence, match_method (alias | retriever | llm | user)
  confirmed_by_user, first_seen, last_seen

line_enrichment
  line_id -> product_id
  qty, unit_price, extended_price
  weight_g               (weighed items)
  cost_per_serving, cost_per_100g
  match_method, confidence
```

---

## 6. Recommended strategy for this corpus

1. Primary: local FDC Branded CSV (Dec 2025, 427 MB zip) loaded into DynamoDB/SQLite, filtered to brandName/brandOwner in {Kirkland Signature, Good & Gather, Market Pantry, Sprouts, Kroger, Simple Truth, Trader Joe's} plus a GTIN index and the Foundation/SR Legacy tables for produce. Public domain, no rate limits, no attribution risk. Refresh twice a year.
2. Trader Joe's: dedicated SKU cache built from the GraphQL query in 2.1 via a browser-context fetch (Chrome MCP or a real-browser worker), ~4K SKUs once, then incremental. FDC has ~200 TJ records and OFF ~4.8K crowdsourced ones of mixed completeness (25 of 28 under one tag had kcal), so this is the only reliable TJ source.
3. Fallback 1: Open Food Facts Parquet from Hugging Face, filtered to the US brands above. Covers Kirkland and Wild Fork gaps. Keep private or accept ODbL.
4. Fallback 2: Kroger Products API for Smith's receipts to obtain UPCs (10K calls/day is far above the Smith's line volume), then FDC by GTIN.
5. Fallback 3: FatSecret Basic (free, 5K/day, barcode and name search) for the residue. Skip Nutritionix (opaque pricing, no bulk, caching restrictions), Edamam (terms forbid saving), Chomp, and Spoonacular at this scale.
6. Matching: per-merchant catalog retrieval + LLM re-rank + user-confirmed alias table (section 3.3).

---

## 7. Risks

- Trader Joe's GraphQL is unofficial, its terms prohibit copying, and bot defense can break the fetch path at any time. Keep the cache authoritative and the fetch path replaceable; keep volume low.
- FDC store-brand coverage is thinner than the 2.0M headline suggests, and `brand_owner` is a supplier name for private label. Filter on `brand_name` text; expect misses for Sprouts and Wild Fork.
- Servings per container is derived, not stored, in FDC and OFF; `package_weight` is free text. Expect parse failures; mark derived values.
- OFF share-alike constrains publishing derived tables on the public site.
- Kroger gives no nutrition; it is only a UPC bridge.
- Weighed produce and deli items need Foundation/SR Legacy data and a separate per-100 g cost path.
- DEMO_KEY behaves as 10 requests/hour in practice; get a real data.gov key, and prefer the CSV over batch API calls.
- Nutritionix pricing and free-tier status are contradictory across sources; do not plan around it.
