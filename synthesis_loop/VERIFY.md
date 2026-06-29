# Synthetic candidate verification (objective, reproducible)

Verified **6** candidates. Mean check pass-rate: **0.833**

| candidate | score | bbox | overlap | spacing | header | 1pay | garble | arith |
|---|---|---|---|---|---|---|---|---|
| `sprouts-arithmetic-2-remove-line-item-00` | 0.857 | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| `sprouts-arithmetic-2-remove-line-item-ae` | 0.714 | ✅ | ✅ | ❌ | ✅ | ❌ | ✅ | ✅ |
| `sprouts-arithmetic-2-remove-line-item-d4` | 1.0 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `sprouts-arithmetic-2-remove-line-item-ef` | 0.857 | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| `sprouts-arithmetic-2-remove-line-item-6e` | 0.857 | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| `sprouts-arithmetic-2-remove-line-item-1b` | 0.714 | ✅ | ✅ | ❌ | ✅ | ❌ | ✅ | ✅ |

## Aggregate
```
{
  "bbox_valid": "6/6",
  "no_overlap": "6/6",
  "word_spacing": "1/6",
  "single_header": "6/6",
  "single_payment_block": "4/6",
  "no_garble": "6/6",
  "arithmetic": "6/6"
}
```
