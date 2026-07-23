# full_fidelity_eval -- trader_joe_s

- merchant: Trader Joe's
- receipt: 4c262079-4fec-4724-a8e1-2886f38ea454#1
- git: `894a903ff600`
- truth: `trader_joe_s` v1 `557c433fcc1e5d74ca66a54f194cf4deca9e39976d26412dc0e1b5ebd778c418` (online-active)
- atlas: `037dc0610504a9b9`

## OVERALL: FAIL

| metric | verdict |
|---|---|
| columns | PASS |
| style | FAIL |
| tokens | PASS |
| separators | PASS |
| graphics | PASS |
| logo | FAIL |
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
  "n_items": 15,
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
       "lane_mid_y_px": 814.5,
       "lane_x_px": 738.63,
       "median_dev_px": -6.64,
       "n_rows": 17,
       "outlier_frac": 0.0,
       "tilt_px_per_100px": -0.592,
       "wobble_iqr_px": 0.27
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
  "area_ratio": 0.313,
  "center_offset_frac": 0.2158,
  "real": {
   "area": 13912.0,
   "cx": 30.5,
   "cy": 118.5,
   "h": 238.0,
   "w": 62.0
  },
  "size_ratio": 0.265,
  "synth": {
   "area": 4360.0,
   "cx": 194.5,
   "cy": 54.0,
   "h": 63.0,
   "w": 116.0
  },
  "verdict": "FAIL",
  "width_ratio": 1.871
 },
 "overall": "FAIL",
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
   "synth": 0.1068
  },
  "classes": [
   {
    "class": "footer",
    "missing_style": [
     "bold"
    ],
    "real": {
     "bold": 1,
     "n": 2,
     "underline": 0
    },
    "synth": {
     "bold": 0,
     "n": 2,
     "underline": 0
    },
    "verdict": "FAIL"
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
  "verdict": "FAIL"
 },
 "tokens": {
  "composed": false,
  "ink_checked": 138,
  "ink_evidence_missing": false,
  "ink_missing_tokens": [
   "REPRINT"
  ],
  "ink_recall": 0.9928,
  "missing_tokens": [],
  "precision_warn": false,
  "text_precision": 1.0,
  "text_recall": 1.0,
  "verdict": "PASS"
 }
}
```
