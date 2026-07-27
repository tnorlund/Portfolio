# full_fidelity_eval -- trader_joe_s

- merchant: Trader Joe's
- receipt: 4c262079-4fec-4724-a8e1-2886f38ea454#1
- git: `8324e542c9e5`
- truth: `trader_joe_s` v1 `557c433fcc1e5d74ca66a54f194cf4deca9e39976d26412dc0e1b5ebd778c418` (online-active)
- atlas: `037dc0610504a9b9`

## OVERALL: PASS_WITH_GAPS

| metric | verdict |
|---|---|
| columns | PASS |
| style | PASS_WITH_GAPS |
| tokens | PASS |
| separators | PASS |
| graphics | PASS |
| logo | PASS |
| arithmetic | PASS |

```json
{
 "arithmetic": {
  "identities": [
   {
    "detail": "",
    "lhs": 63.66,
    "name": "total_eq_tender",
    "rhs": 63.66,
    "status": "HOLDS"
   }
  ],
  "n_items": 14,
  "summary": {
   "tender": 63.66,
   "total": 63.66
  },
  "testable": 1,
  "verdict": "PASS",
  "violated": 0
 },
 "columns": {
  "bands": {
   "ALL": {
    "cell_w_px": 16.61,
    "columns": [
     {
      "abs_drift_limit_px": 24.91,
      "abs_drift_px": 1.0,
      "column": {
       "anchor": "right",
       "role": "amount",
       "spread": 0.0003,
       "support": 17,
       "x": 0.9811
      },
      "outlier_limit": 0.15,
      "real": {
       "lane_mid_y_px": 814.5,
       "lane_x_px": 740.62,
       "median_dev_px": -5.64,
       "n_rows": 17,
       "outlier_frac": 0.0,
       "tilt_px_per_100px": 0.976,
       "wobble_iqr_px": 0.65
      },
      "shear_px_per_100px": 1.57,
      "synth": {
       "lane_mid_y_px": 823.0,
       "lane_x_px": 738.65,
       "median_dev_px": -6.64,
       "n_rows": 17,
       "outlier_frac": 0.0,
       "tilt_px_per_100px": -0.599,
       "wobble_iqr_px": 0.29
      },
      "verdict": "PASS",
      "wobble_limit_px": 3.8
     }
    ],
    "lane_gaps": [],
    "source": "bootstrap",
    "untested_roles": [],
    "verdict": "PASS"
   }
  },
  "verdict": "PASS"
 },
 "coverage_gaps": [
  "style",
  "style:total_line"
 ],
 "graphics": {
  "matched": [],
  "missing_in_synth": [],
  "phantom_in_synth": [],
  "real": [],
  "synth": [],
  "verdict": "PASS"
 },
 "logo": {
  "area_ratio": 1.173,
  "center_offset_frac": 0.0329,
  "real": {
   "area": 18139.0,
   "cx": 410.5,
   "cy": 48.0,
   "h": 69.0,
   "w": 612.0
  },
  "size_ratio": 0.971,
  "synth": {
   "area": 21272.0,
   "cx": 385.5,
   "cy": 55.0,
   "h": 67.0,
   "w": 600.0
  },
  "verdict": "PASS",
  "width_ratio": 0.98
 },
 "overall": "PASS_WITH_GAPS",
 "separators": {
  "kind_mismatches": 0,
  "matched": [
   {
    "dy": 0.001,
    "real": {
     "height_px": 2,
     "kind": "dash",
     "y_frac": 0.582
    },
    "synth": {
     "height_px": 2,
     "kind": "dash",
     "y_frac": 0.583
    }
   }
  ],
  "missing_in_synth": [],
  "phantom_in_synth": [],
  "real_count": 1,
  "synth_count": 1,
  "verdict": "PASS"
 },
 "style": {
  "body_stroke_fail": false,
  "body_stroke_rel": {
   "real": 0.1068,
   "synth": 0.1565
  },
  "classes": [
   {
    "class": "footer",
    "real": {
     "bold": 1,
     "n": 2,
     "underline": 0
    },
    "synth": {
     "bold": 1,
     "n": 2,
     "underline": 0
    },
    "verdict": "PASS"
   },
   {
    "class": "payment",
    "real": {
     "bold": 0,
     "n": 5,
     "underline": 0
    },
    "synth": {
     "bold": 0,
     "n": 5,
     "underline": 0
    },
    "verdict": "PASS"
   },
   {
    "class": "total_line",
    "real": {
     "bold": 0,
     "n": 1,
     "underline": 0
    },
    "synth": {
     "bold": 0,
     "n": 1,
     "underline": 0
    },
    "verdict": "UNTESTED"
   }
  ],
  "untested_classes": [
   "total_line"
  ],
  "verdict": "PASS_WITH_GAPS"
 },
 "tokens": {
  "composed": false,
  "ink_checked": 138,
  "ink_evidence_missing": false,
  "ink_missing_tokens": [],
  "ink_recall": 1.0,
  "missing_tokens": [],
  "precision_warn": false,
  "text_precision": 1.0,
  "text_recall": 1.0,
  "verdict": "PASS"
 }
}
```
