# Per-merchant online catalogs

Each `<merchant_slug>.json` supplies a merchant's ONLINE-storefront products for
the `compose_online_catalog` synthesis operation. Files merge with the in-code
`_MERCHANT_ONLINE_CATALOG`; one file per merchant keeps parallel contributions
conflict-free.

Schema:

```json
{
  "merchant_name": "Costco Wholesale",
  "source_url": "https://www.costco.com/...",
  "entries": [
    {"name": "KS ORGANIC EGGS 24CT", "price": "7.99", "upc": "000000000000", "taxable": false}
  ]
}
```

- `merchant_name` must match the merchant's canonical name (case-insensitive).
- `price` is a string or number (dollars). `upc` optional. `taxable` defaults true.
- Use real product names + current storefront prices; mark grocery staples
  `taxable: false` where the jurisdiction exempts them.
