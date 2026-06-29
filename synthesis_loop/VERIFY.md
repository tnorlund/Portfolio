# Synthetic candidate verification (objective, reproducible)

Verified **6** candidates. Mean check pass-rate: **0.7**

| candidate | score | bbox | overlap | header | garble | arith |
|---|---|---|---|---|---|---|
| `sprouts-arithmetic-2-remove-line-item-00` | 0.8 | ✅ | ❌ | ✅ | ✅ | ✅ |
| `sprouts-arithmetic-2-remove-line-item-ae` | 0.6 | ✅ | ❌ | ❌ | ✅ | ✅ |
| `sprouts-arithmetic-2-remove-line-item-d4` | 0.6 | ✅ | ❌ | ❌ | ✅ | ✅ |
| `sprouts-arithmetic-2-remove-line-item-ef` | 0.8 | ✅ | ✅ | ❌ | ✅ | ✅ |
| `sprouts-arithmetic-2-remove-line-item-6e` | 0.6 | ✅ | ❌ | ❌ | ✅ | ✅ |
| `sprouts-arithmetic-2-remove-line-item-1b` | 0.8 | ✅ | ✅ | ❌ | ✅ | ✅ |

## Aggregate
```
{
  "bbox_valid": "6/6",
  "no_overlap": "2/6",
  "single_header": "1/6",
  "no_garble": "6/6",
  "arithmetic": "6/6"
}
```
