# Basic CI Monitoring - Phase 1

This document describes the basic CI monitoring implementation, the first phase of a comprehensive CI optimization strategy.

## Overview

This phase introduces foundational CI performance monitoring without disrupting existing workflows. It focuses on:

1. **Basic Metrics Collection** - System resource usage and timing data
2. **Simple Analysis** - Identify trends in CI performance
3. **Automated Reporting** - Weekly performance summaries

## Components

### CI Profiler (`scripts/ci_profiler_simple.py`)

A lightweight Python script that collects basic system metrics:

- CPU and memory usage
- System information
- Job timing data
- Basic trend analysis

**Usage:**
```bash
# Collect metrics for a specific job
python scripts/ci_profiler.py --collect --job-name "test-job"

# Analyze last 7 days of data
python scripts/ci_profiler.py --analyze --days 7

# Generate performance report
python scripts/ci_profiler.py --report
```

### Monitoring Workflow (`.github/workflows/ci-monitoring-basic.yml`)

Automated GitHub Actions workflow that:

- Runs after main CI workflows complete
- Collects performance metrics weekly
- Generates trend analysis
- Creates performance reports

**Triggers:**
- After main CI workflows (for data collection)
- Weekly schedule (Sundays at 2 AM UTC)
- Manual dispatch

## Integration

### Adding to Existing Workflows

To integrate with your existing CI workflows, add this step to collect metrics:

```yaml
- name: Collect CI Metrics
  if: always()  # Run even if tests fail
  run: |
    python scripts/ci_profiler.py --collect --job-name "${{ github.job }}"
```

### Viewing Results

1. **Artifacts**: Check GitHub Actions artifacts for detailed data
2. **Weekly Reports**: Automated reports uploaded as artifacts
3. **Manual Analysis**: Run scripts locally for immediate insights

## Benefits

- **Zero Impact**: Doesn't change existing CI behavior
- **Baseline Metrics**: Establishes performance baselines
- **Trend Detection**: Identifies performance degradation over time
- **Foundation**: Prepares for advanced optimizations in future phases

## Expected Metrics

- **System Resource Usage**: CPU, memory, disk utilization
- **Job Frequency**: How often different jobs run
- **Basic Trends**: Performance over time
- **System Info**: Environment consistency tracking

## Next Phases

This basic monitoring enables:

1. **Phase 2**: Smart caching based on usage patterns
2. **Phase 3**: Test optimization using execution data
3. **Phase 4**: Advanced recommendations and alerting

## Getting Started

1. **Deploy**: Merge this phase to start collecting data
2. **Monitor**: Check weekly reports after 1-2 weeks
3. **Baseline**: Establish normal performance ranges
4. **Plan**: Use data to prioritize Phase 2 optimizations

## Troubleshooting

**No metrics collected:**
- Check if Python dependencies are installed
- Verify the script has write permissions to `ci-metrics/`

**Missing reports:**
- Check GitHub Actions artifacts in the workflow runs
- Ensure the monitoring workflow is enabled

**Large artifact sizes:**
- Metrics are cleaned up automatically after 30 days
- Each metrics file is small (< 1KB typically)

This basic monitoring provides immediate value while laying the groundwork for more advanced CI optimizations.