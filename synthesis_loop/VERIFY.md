# Synthetic candidate verification (objective, reproducible)

Verified **6** candidates. Mean check pass-rate: **0.938**

| candidate | score | bbox | overlap | spacing | header | 1pay | garble | arith | labels |
|---|---|---|---|---|---|---|---|---|---|
| `sprouts-arithmetic-2-remove-line-item-00` | 0.875 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| `sprouts-arithmetic-2-remove-line-item-ae` | 1.0 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `sprouts-arithmetic-2-remove-line-item-d4` | 0.875 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| `sprouts-arithmetic-2-remove-line-item-ef` | 1.0 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `sprouts-arithmetic-2-remove-line-item-6e` | 0.875 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| `sprouts-arithmetic-2-remove-line-item-1b` | 1.0 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

## Aggregate
```
{
  "bbox_valid": "6/6",
  "no_overlap": "6/6",
  "word_spacing": "6/6",
  "single_header": "6/6",
  "single_payment_block": "6/6",
  "no_garble": "6/6",
  "arithmetic": "6/6",
  "labels_valid": "3/6"
}
```
