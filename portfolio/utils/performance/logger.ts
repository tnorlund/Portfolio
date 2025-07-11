import { PerformanceMetrics } from './monitor';

export interface PerformanceLog {
  timestamp: string;
  metrics: PerformanceMetrics;
  url: string;
  userAgent: string;
}

export class PerformanceLogger {
  private logs: PerformanceLog[] = [];
  private maxLogs = 100;

  log(metrics: PerformanceMetrics) {
    if (typeof window === 'undefined') return;

    const log: PerformanceLog = {
      timestamp: new Date().toISOString(),
      metrics,
      url: window.location.href,
      userAgent: navigator.userAgent,
    };

    this.logs.push(log);
    
    // Keep only the latest logs
    if (this.logs.length > this.maxLogs) {
      this.logs = this.logs.slice(-this.maxLogs);
    }

    // Store in localStorage for persistence
    this.saveToLocalStorage();
    
    // Log to console in development
    if (process.env.NODE_ENV === 'development') {
      this.logToConsole(log);
    }
  }

  private saveToLocalStorage() {
    try {
      localStorage.setItem('performance-logs', JSON.stringify(this.logs));
    } catch (e) {
      console.warn('Failed to save performance logs to localStorage');
    }
  }

  loadFromLocalStorage() {
    try {
      const stored = localStorage.getItem('performance-logs');
      if (stored) {
        this.logs = JSON.parse(stored);
      }
    } catch (e) {
      console.warn('Failed to load performance logs from localStorage');
    }
  }

  private logToConsole(log: PerformanceLog) {
    const { metrics } = log;
    
    console.group(`[Performance] ${new Date(log.timestamp).toLocaleTimeString()}`);
    
    // Core Web Vitals
    if (metrics.lcp || metrics.fid || metrics.cls || metrics.fcp || metrics.ttfb) {
      console.group('Core Web Vitals');
      if (metrics.lcp) console.log(`LCP: ${metrics.lcp.toFixed(2)}ms`);
      if (metrics.fid) console.log(`FID: ${metrics.fid.toFixed(2)}ms`);
      if (metrics.cls) console.log(`CLS: ${metrics.cls.toFixed(3)}`);
      if (metrics.fcp) console.log(`FCP: ${metrics.fcp.toFixed(2)}ms`);
      if (metrics.ttfb) console.log(`TTFB: ${metrics.ttfb.toFixed(2)}ms`);
      console.groupEnd();
    }

    // Memory usage
    if (metrics.usedJSHeapSize && metrics.jsHeapSizeLimit) {
      const usedMB = (metrics.usedJSHeapSize / (1024 * 1024)).toFixed(1);
      const limitMB = (metrics.jsHeapSizeLimit / (1024 * 1024)).toFixed(1);
      console.log(`Memory: ${usedMB}MB / ${limitMB}MB`);
    }

    // Component render times
    if (metrics.componentRenderTime && Object.keys(metrics.componentRenderTime).length > 0) {
      console.group('Component Renders');
      Object.entries(metrics.componentRenderTime).forEach(([name, times]) => {
        const avgTime = times.reduce((a, b) => a + b, 0) / times.length;
        console.log(`${name}: ${avgTime.toFixed(1)}ms (${times.length} renders)`);
      });
      console.groupEnd();
    }

    // API calls
    if (metrics.apiCallDuration && Object.keys(metrics.apiCallDuration).length > 0) {
      console.group('API Calls');
      Object.entries(metrics.apiCallDuration).forEach(([endpoint, times]) => {
        const avgTime = times.reduce((a, b) => a + b, 0) / times.length;
        console.log(`${endpoint}: ${avgTime.toFixed(0)}ms (${times.length} calls)`);
      });
      console.groupEnd();
    }

    console.groupEnd();
  }

  getLogs(): PerformanceLog[] {
    return [...this.logs];
  }

  clear() {
    this.logs = [];
    try {
      localStorage.removeItem('performance-logs');
    } catch (e) {
      // Ignore
    }
  }

  generateReport(): string {
    if (this.logs.length === 0) {
      return 'No performance data available';
    }

    const report: string[] = ['# Performance Report\n'];
    report.push(`Generated: ${new Date().toISOString()}\n`);
    report.push(`Total logs: ${this.logs.length}\n`);

    // Aggregate metrics
    const aggregated = this.aggregateMetrics();
    
    report.push('\n## Core Web Vitals Summary\n');
    report.push('| Metric | Average | Min | Max | P75 | P95 |');
    report.push('|--------|---------|-----|-----|-----|-----|');
    
    ['lcp', 'fid', 'cls', 'fcp', 'ttfb'].forEach(metric => {
      const stats = aggregated[metric as keyof typeof aggregated];
      if (stats) {
        report.push(
          `| ${metric.toUpperCase()} | ${stats.avg} | ${stats.min} | ${stats.max} | ${stats.p75} | ${stats.p95} |`
        );
      }
    });

    // Component performance
    if (aggregated.componentRenderTime) {
      report.push('\n## Component Performance\n');
      report.push('| Component | Avg Render Time | Total Renders |');
      report.push('|-----------|-----------------|---------------|');
      
      Object.entries(aggregated.componentRenderTime)
        .sort((a, b) => (b[1] as any).avg - (a[1] as any).avg)
        .forEach(([name, stats]: [string, any]) => {
          report.push(`| ${name} | ${stats.avg}ms | ${stats.count} |`);
        });
    }

    // API performance
    if (aggregated.apiCallDuration) {
      report.push('\n## API Performance\n');
      report.push('| Endpoint | Avg Duration | Total Calls |');
      report.push('|----------|--------------|-------------|');
      
      Object.entries(aggregated.apiCallDuration)
        .sort((a, b) => (b[1] as any).avg - (a[1] as any).avg)
        .forEach(([endpoint, stats]: [string, any]) => {
          report.push(`| ${endpoint} | ${stats.avg}ms | ${stats.count} |`);
        });
    }

    return report.join('\n');
  }

  private aggregateMetrics() {
    const metrics: any = {};
    
    // Helper to calculate stats
    const calculateStats = (values: number[]) => {
      if (values.length === 0) return null;
      
      const sorted = values.sort((a, b) => a - b);
      const sum = sorted.reduce((a, b) => a + b, 0);
      
      return {
        avg: Math.round(sum / sorted.length),
        min: Math.round(sorted[0]),
        max: Math.round(sorted[sorted.length - 1]),
        p75: Math.round(sorted[Math.floor(sorted.length * 0.75)]),
        p95: Math.round(sorted[Math.floor(sorted.length * 0.95)]),
      };
    };

    // Aggregate core web vitals
    ['lcp', 'fid', 'cls', 'fcp', 'ttfb'].forEach(metric => {
      const values = this.logs
        .map(log => log.metrics[metric as keyof PerformanceMetrics])
        .filter(v => v !== undefined) as number[];
      
      const stats = calculateStats(values);
      if (stats) {
        metrics[metric] = stats;
      }
    });

    // Aggregate component render times
    const componentTimes: Record<string, number[]> = {};
    this.logs.forEach(log => {
      if (log.metrics.componentRenderTime) {
        Object.entries(log.metrics.componentRenderTime).forEach(([name, times]) => {
          if (!componentTimes[name]) componentTimes[name] = [];
          componentTimes[name].push(...times);
        });
      }
    });

    metrics.componentRenderTime = {};
    Object.entries(componentTimes).forEach(([name, times]) => {
      metrics.componentRenderTime[name] = {
        avg: Math.round(times.reduce((a, b) => a + b, 0) / times.length),
        count: times.length,
      };
    });

    // Aggregate API call durations
    const apiTimes: Record<string, number[]> = {};
    this.logs.forEach(log => {
      if (log.metrics.apiCallDuration) {
        Object.entries(log.metrics.apiCallDuration).forEach(([endpoint, times]) => {
          if (!apiTimes[endpoint]) apiTimes[endpoint] = [];
          apiTimes[endpoint].push(...times);
        });
      }
    });

    metrics.apiCallDuration = {};
    Object.entries(apiTimes).forEach(([endpoint, times]) => {
      metrics.apiCallDuration[endpoint] = {
        avg: Math.round(times.reduce((a, b) => a + b, 0) / times.length),
        count: times.length,
      };
    });

    return metrics;
  }

  downloadReport() {
    const report = this.generateReport();
    const blob = new Blob([report], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `performance-report-${new Date().toISOString()}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }
}

// Singleton instance
let performanceLogger: PerformanceLogger | null = null;

export function getPerformanceLogger(): PerformanceLogger | null {
  if (!performanceLogger && typeof window !== 'undefined') {
    performanceLogger = new PerformanceLogger();
    performanceLogger.loadFromLocalStorage();
  }
  return performanceLogger;
}