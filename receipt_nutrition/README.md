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
