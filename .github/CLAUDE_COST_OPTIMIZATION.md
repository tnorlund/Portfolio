# Claude AI Review Cost Optimization

## Quick Start
**Budget**: $25/month | **Daily Limit**: $5 | **Default**: Haiku

## Model Selection
| Model | Cost | Use Case |
|-------|------|----------|
| **Haiku** | $0.01-0.05 | Small PRs, tests, quick fixes |
| **Sonnet** | $0.15-0.75 | Medium PRs, standard features |
| **Opus** | $0.75-3.75 | Security, architecture, large PRs |

## Current Configuration
```json
{
  "monthly_budget": 25.0,
  "daily_limit": 5.0,
  "default_model": "haiku",
  "model_selection": {
    "lines_threshold_sonnet": 200,
    "lines_threshold_opus": 1000
  }
}
```

## Cost Controls
- ✅ **Fast Validation Gate**: Saves 60-80% of AI costs
- ✅ **Budget Enforcement**: Auto-stop at limits
- ✅ **Smart Model Selection**: Based on PR size/complexity
- ✅ **Real-time Tracking**: `python scripts/cost_optimizer.py --report`

## Expected Costs
- **Typical month**: $5-15 (mostly Haiku)
- **ROI**: 10-50x through bug prevention and time savings