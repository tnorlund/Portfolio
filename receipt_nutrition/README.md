# receipt_nutrition

Evidence-first product facts and dimensional purchase costing. Product
identity, quantity resolution, and nutrient completeness remain separate.
Unknown quantities never default to one and absent nutrients never become zero.

The initial milestone supplies immutable validated inputs, Decimal costing,
explicit-unit evidence recovery, and a synthetic contract harness. It does
not establish real product coverage, model accuracy, or deployed behavior.

From the repository root after installing the local package:

```sh
.venv/bin/python -m pytest receipt_nutrition/tests
.venv/bin/python scripts/nutrition_harness/evaluate.py --mode contract
```

Inputs use decimal strings, integers, or Decimal, never binary floats. Amounts
are canonical g/ml/each; purchase quantities may also count packages. Label
servings/container take precedence over a derived compatible ratio. A
per-serving fact scales with supported servings; per-100-g/ml facts scale
only with evidenced mass/volume. Rounded label counts and physical mass may
therefore produce different scaling, and each result retains its basis.

Conversion between mass, volume, and each requires product-specific evidence.
Money rounds HALF_UP to cents only for display; nutrient calculations retain
their decimal precision. The unit adapter checks existing parsed quantity and
price instead of replacing the decoder. Missing unit evidence stays unknown.

Receipt unit recovery requires a complete quantity/rate annotation line. A
product name may be on a separate line. Mixed name/quantity lines, partial
numeric tokens, and unsupported denominators remain unknown.

## Private portion calculation

`receipt_nutrition.portions` works offline with a private input JSON file.
It does not call AWS, a model, a catalog service, or an external website.
A `Meal` has a title and uniquely keyed items. Each `MealItem` contains a
validated `Product`, `Purchase`, `Portion`, optional `HouseholdServing`, and
explicit assumption notes. Decimal values are strings. See the synthetic
example builder in `tests/test_portions.py` for a complete input shape.

```sh
.venv/bin/python -m receipt_nutrition.portions /private/path/meal.json
.venv/bin/python -m receipt_nutrition.portions /private/path/meal.json --format json
.venv/bin/python -m receipt_nutrition.portions /private/path/meal.json --quantity eggs=12:each --portion butter=1:tbsp
```

Portions support serving, package, each, g, ml, tsp and tbsp. Teaspoon and
tablespoon inputs require a verified household serving equivalence for that
product; one US tablespoon is three teaspoons. Purchase quantity is a
separate input: changing carton count changes cost, not the nutrients in
three eggs. Unknown purchase quantity leaves cost unknown while a supported
portion can still have known nutrients. These are scenario calculations,
not a consumption log or a measurement of pan residue.

The Markdown and JSON results expose sources, assumptions and input hash.
Complete nutrient totals remain null when an item lacks a fact, while
available subtotals retain their coverage. Stored assumption notes describe
the base scenario; the effective purchase quantity and unit are printed with
their basis, and explicit CLI overrides take precedence over original notes.
The total cost rounds once from exact ratios, so it may differ by a cent
from the sum of individually rounded display costs.

Keep actual receipt prices, personal meal inputs and generated reports in a
private directory outside the repository and public site assets. The tests
contain synthetic prices and labels only. No package-of-one default or
unverified label values are silently introduced.

Package and serving bases must agree. The default rounding allowance is zero.
For nominal rounded labels, `MealItem.serving_rounding_allowance` is an explicit
calculation assumption, expressed per serving in its physical unit and printed
in the report. It is capped at the smaller of 0.5 g/ml and 5% of serving size;
counts have no allowance. This is a conservative consistency policy, not a
claim about legal labelling tolerances. Decimal formatting does not change it.
Measured portions use compatible physical purchase/net amounts for cost;
household portions use the declared label-serving count. Generic matches remain
labelled estimates in both the readable report and JSON.

## Seeding from a pilot lookup file

`scripts/seed_nutrition_pilot.py` writes `FoodProduct` revisions and
`ProductAlias` rows from a merged lookup file. It is a dry run unless `--apply`
and a dev `--table` are given, refuses the prod table, never overwrites a
user-confirmed alias, and is idempotent: a second run over the same file
changes nothing. Retailer panels and UPC matches become `matched`, name
proxies stay `matched` at their capped confidence with the proxy note kept,
ambiguous products become `pending` with candidates, non-food lines become
`not_food`. A product whose serving text cannot be parsed keeps its identity
and drops its per-serving facts rather than guessing.
