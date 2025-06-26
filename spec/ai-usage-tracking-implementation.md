# AI Usage Tracking Implementation Specification

## Overview
This specification outlines the best practices implementation for tracking AI API usage across different environments (Production, Staging, CICD, Development) to ensure accurate cost attribution and prevent collision between different types of usage.

## Goals
1. **Environment Isolation**: Separate tracking for CICD, production, staging, and development
2. **Cost Attribution**: Accurately attribute costs to specific projects, workflows, and operations
3. **Budget Management**: Implement cost budgets and alerting
4. **Visibility**: Provide clear reporting and dashboards for usage across environments
5. **Migration Path**: Smooth transition from current implementation without breaking changes

## Architecture

### 1. Environment Configuration

```python
# config/ai_usage_config.py
import os
from enum import Enum

class Environment(Enum):
    PRODUCTION = "production"
    STAGING = "staging"
    CICD = "cicd"
    DEVELOPMENT = "development"

class AIUsageConfig:
    @staticmethod
    def get_config():
        env = os.getenv('ENVIRONMENT', 'development')
        
        return {
            'environment': env,
            'table_suffix': f"-{env}" if env != 'production' else '',
            'require_context': env == 'production',  # Strict in prod
            'auto_tag': {
                'cicd': {
                    'run_id': os.getenv('GITHUB_RUN_ID'),
                    'workflow': os.getenv('GITHUB_WORKFLOW'),
                    'actor': os.getenv('GITHUB_ACTOR'),
                },
                'common': {
                    'service': 'receipt-processing',
                    'version': os.getenv('APP_VERSION', 'unknown')
                }
            }
        }
```

### 2. Context Manager Pattern

```python
# utils/ai_usage_context.py
from contextlib import contextmanager
from datetime import datetime

@contextmanager
def ai_usage_context(operation_type, **kwargs):
    """Ensures consistent context across AI operations"""
    tracker = get_ai_tracker()
    context = {
        'operation_type': operation_type,
        'start_time': datetime.utcnow(),
        **kwargs
    }
    
    try:
        tracker.set_context(context)
        yield tracker
    finally:
        # Auto-save metrics on context exit
        tracker.flush_metrics()
```

### 3. Enhanced Tracker Implementation

```python
# utils/ai_usage_tracker_enhanced.py
class AIUsageTracker:
    def __init__(self, storage_backend=None, environment=None):
        self.config = AIUsageConfig.get_config()
        self.environment = environment or self.config['environment']
        self.storage_backend = storage_backend or self._get_storage_backend()
        self.current_context = {}
        
    def _get_storage_backend(self):
        table_name = f"ai-usage-metrics{self.config['table_suffix']}"
        return DynamoStorageBackend(table_name)
    
    def set_context(self, context):
        """Set the current tracking context"""
        self.current_context = {
            **self._get_base_context(),
            **context
        }
    
    def _get_base_context(self):
        """Get automatic context based on environment"""
        base = {
            'environment': self.environment,
            'timestamp': datetime.utcnow().isoformat(),
            **self.config['auto_tag']['common']
        }
        
        if self.environment == 'cicd':
            base.update(self.config['auto_tag']['cicd'])
            
        return base
```

## Implementation Phases

### Phase 1: Environment Tagging (Week 1)
- Add environment configuration to existing codebase
- Update AIUsageTracker to include environment in all metrics
- Deploy to staging for testing

### Phase 2: Context Manager Adoption (Week 2)
- Implement context manager pattern
- Update new features to use context manager
- Create migration guide for existing code

### Phase 3: CICD Integration (Week 3)
- Add environment variables to CICD pipelines
- Implement CICD-specific tracking
- Create daily usage reports in GitHub Actions

### Phase 4: Monitoring & Alerts (Week 4)
- Implement CostMonitor class
- Set up daily budget alerts
- Create Slack/email notifications

### Phase 5: API Key Separation (Week 5)
- Create separate API keys per environment
- Update secrets management
- Implement key rotation policy

## Usage Examples

### Production Usage
```python
async def process_receipt_batch(batch_id, receipts):
    with ai_usage_context(
        operation_type='batch_processing',
        batch_id=batch_id,
        receipt_count=len(receipts)
    ) as tracker:
        for receipt in receipts:
            result = await tracker.track_openai_completion(
                model="gpt-4",
                messages=[...],
                context={'receipt_id': receipt.id}
            )
```

### CICD Usage
```python
# In test files
def test_receipt_processing():
    with ai_usage_context(
        operation_type='integration_test',
        test_name='test_receipt_processing'
    ) as tracker:
        # Test code that uses AI services
        result = tracker.track_anthropic_completion(...)
```

## Cost Monitoring

### Budget Configuration
```yaml
# config/budgets.yaml
budgets:
  daily:
    production: 100.00
    staging: 20.00
    cicd: 50.00
    development: 10.00
  
  alerts:
    thresholds:
      - percent: 80
        severity: warning
      - percent: 95
        severity: critical
    
    channels:
      - type: slack
        webhook: ${SLACK_WEBHOOK_URL}
      - type: email
        recipients:
          - devops@example.com
```

### Monitoring Implementation
```python
# monitoring/cost_monitor.py
class CostMonitor:
    def __init__(self, config_path='config/budgets.yaml'):
        self.config = load_yaml(config_path)
        
    async def check_all_environments(self):
        for env, budget in self.config['budgets']['daily'].items():
            await self.check_environment_budget(env, budget)
    
    async def check_environment_budget(self, environment, daily_budget):
        today_cost = await AIUsageMetric.get_daily_cost(
            date=datetime.utcnow().date(),
            environment=environment
        )
        
        for threshold in self.config['alerts']['thresholds']:
            if today_cost > (daily_budget * threshold['percent'] / 100):
                await self.send_alert(
                    environment=environment,
                    severity=threshold['severity'],
                    message=f"AI usage at ${today_cost:.2f} "
                           f"({today_cost/daily_budget:.0%} of daily budget)"
                )
```

## Reporting

### Daily Report Structure
```json
{
  "date": "2024-01-15",
  "summary": {
    "total_cost": 125.50,
    "by_environment": {
      "production": 75.00,
      "cicd": 40.00,
      "staging": 10.00,
      "development": 0.50
    },
    "by_service": {
      "openai": 100.00,
      "anthropic": 20.00,
      "google_places": 5.50
    }
  },
  "top_operations": [
    {
      "operation_type": "batch_processing",
      "cost": 50.00,
      "count": 1000,
      "avg_cost": 0.05
    }
  ],
  "anomalies": [
    {
      "type": "spike",
      "environment": "cicd",
      "description": "300% increase in usage compared to 7-day average"
    }
  ]
}
```

### Dashboard Implementation
```python
# scripts/usage_dashboard.py
import click
from datetime import datetime, timedelta
from collections import defaultdict

@click.command()
@click.option('--days', default=7, help='Number of days to analyze')
@click.option('--environment', default='all', help='Environment to filter')
@click.option('--format', type=click.Choice(['console', 'json', 'html']), default='console')
def generate_dashboard(days, environment, format):
    """Generate AI usage dashboard"""
    
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)
    
    # Get metrics
    metrics = AIUsageMetric.query_by_date_range(
        start_date=start_date,
        end_date=end_date,
        environment=None if environment == 'all' else environment
    )
    
    # Generate report
    report = generate_report(metrics)
    
    # Output in requested format
    if format == 'console':
        print_console_report(report)
    elif format == 'json':
        save_json_report(report)
    elif format == 'html':
        generate_html_report(report)
```

## CICD Pipeline Integration

### GitHub Actions Workflow
```yaml
# .github/workflows/ai-usage-report.yml
name: AI Usage Report
on:
  schedule:
    - cron: '0 0 * * *'  # Daily at midnight
  workflow_dispatch:
    inputs:
      days:
        description: 'Number of days to report'
        required: false
        default: '1'

env:
  ENVIRONMENT: cicd

jobs:
  report:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.9'
      
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
      
      - name: Generate Usage Report
        run: |
          python -m scripts.generate_usage_report \
            --environment cicd \
            --days ${{ github.event.inputs.days || '1' }} \
            --output reports/
      
      - name: Check Budget
        run: |
          python -m scripts.check_budget \
            --environment cicd \
            --fail-on-exceeded
      
      - name: Upload Report
        uses: actions/upload-artifact@v3
        with:
          name: ai-usage-report-${{ github.run_id }}
          path: reports/
      
      - name: Post to Slack
        if: always()
        env:
          SLACK_WEBHOOK: ${{ secrets.SLACK_WEBHOOK }}
        run: |
          python -m scripts.post_usage_summary \
            --webhook $SLACK_WEBHOOK \
            --environment cicd
```

## Testing Strategy

### Unit Tests
```python
# tests/test_ai_usage_isolation.py
import pytest
from unittest.mock import patch
import os

class TestEnvironmentIsolation:
    def test_metrics_isolated_by_environment(self):
        """Ensure metrics don't cross environments"""
        
        # Track in CICD
        with patch.dict(os.environ, {'ENVIRONMENT': 'cicd'}):
            tracker = AIUsageTracker()
            tracker.track_openai_completion(
                model="gpt-4",
                input_tokens=100,
                output_tokens=50
            )
        
        # Track in production
        with patch.dict(os.environ, {'ENVIRONMENT': 'production'}):
            tracker = AIUsageTracker()
            tracker.track_openai_completion(
                model="gpt-4",
                input_tokens=200,
                output_tokens=100
            )
        
        # Verify isolation
        cicd_metrics = AIUsageMetric.query_by_environment('cicd')
        prod_metrics = AIUsageMetric.query_by_environment('production')
        
        assert len(cicd_metrics) == 1
        assert len(prod_metrics) == 1
        assert cicd_metrics[0].environment == 'cicd'
        assert prod_metrics[0].environment == 'production'
    
    def test_context_manager_tracks_all_operations(self):
        """Test context manager captures all operations"""
        
        with ai_usage_context(
            operation_type='test_operation',
            test_id='123'
        ) as tracker:
            # Multiple AI calls
            tracker.track_openai_completion(...)
            tracker.track_anthropic_completion(...)
            tracker.track_google_places_search(...)
        
        # Verify all operations have consistent context
        metrics = AIUsageMetric.query_by_context({'test_id': '123'})
        assert len(metrics) == 3
        assert all(m.context['operation_type'] == 'test_operation' for m in metrics)
```

### Integration Tests
```python
# tests/test_cost_monitoring_integration.py
@pytest.mark.integration
async def test_budget_alert_triggered():
    """Test that budget alerts are triggered correctly"""
    
    # Set a low budget for testing
    monitor = CostMonitor()
    monitor.config['budgets']['daily']['test'] = 1.00
    
    # Track usage that exceeds budget
    with ai_usage_context(operation_type='test') as tracker:
        for _ in range(10):
            tracker.track_openai_completion(
                model="gpt-4",
                input_tokens=1000,
                output_tokens=1000
            )
    
    # Check that alert was triggered
    alerts = await monitor.check_environment_budget('test', 1.00)
    assert len(alerts) > 0
    assert alerts[0]['severity'] == 'critical'
```

## Migration Guide

### For Existing Code
1. **Add environment variable to deployments**
   ```bash
   export ENVIRONMENT=production
   ```

2. **Update tracker initialization**
   ```python
   # Old
   tracker = AIUsageTracker()
   
   # New
   tracker = AIUsageTracker(environment=os.getenv('ENVIRONMENT'))
   ```

3. **Wrap operations in context managers**
   ```python
   # Old
   result = tracker.track_openai_completion(...)
   
   # New
   with ai_usage_context(operation_type='your_operation') as tracker:
       result = tracker.track_openai_completion(...)
   ```

### For New Features
- Always use the context manager pattern
- Include meaningful operation_type and context
- Consider the environment when setting context

## Security Considerations

1. **API Key Management**
   - Use separate keys per environment
   - Rotate keys quarterly
   - Never commit keys to source control
   - Use secrets management service (AWS Secrets Manager, etc.)

2. **Access Control**
   - Limit DynamoDB table access by environment
   - Use IAM roles for service accounts
   - Audit access to usage data

3. **Data Privacy**
   - Don't include PII in tracking context
   - Aggregate data for reporting
   - Set retention policies per environment

## Success Metrics

1. **Adoption**: 100% of AI API calls tracked with environment
2. **Accuracy**: <5% discrepancy between tracked and billed costs
3. **Visibility**: Daily reports available within 1 hour of day end
4. **Reliability**: <1% tracking failures
5. **Performance**: <10ms overhead per tracked operation

## Appendix

### A. Environment Variables Reference
- `ENVIRONMENT`: Current environment (production, staging, cicd, development)
- `GITHUB_RUN_ID`: GitHub Actions run identifier
- `GITHUB_WORKFLOW`: GitHub Actions workflow name
- `GITHUB_ACTOR`: GitHub user who triggered the workflow
- `APP_VERSION`: Application version for tracking

### B. DynamoDB Schema
```json
{
  "TableName": "ai-usage-metrics-{environment}",
  "PartitionKey": "date",
  "SortKey": "timestamp#service#model",
  "Attributes": {
    "environment": "String",
    "service": "String",
    "model": "String",
    "operation_type": "String",
    "cost": "Number",
    "usage": "Map",
    "context": "Map"
  }
}
```

### C. Monitoring Queries
```python
# Top operations by cost
SELECT operation_type, SUM(cost) as total_cost
FROM ai_usage_metrics
WHERE environment = 'production'
  AND date >= CURRENT_DATE - INTERVAL 7 DAYS
GROUP BY operation_type
ORDER BY total_cost DESC
LIMIT 10

# Daily cost trend
SELECT date, environment, SUM(cost) as daily_cost
FROM ai_usage_metrics
WHERE date >= CURRENT_DATE - INTERVAL 30 DAYS
GROUP BY date, environment
ORDER BY date DESC
```