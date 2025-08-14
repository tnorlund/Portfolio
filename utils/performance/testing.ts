/**
 * Performance testing utilities for development
 */

export interface PerformanceTest {
  name: string;
  fn: () => void | Promise<void>;
  iterations?: number;
  warmup?: number;
}

export interface PerformanceTestResult {
  name: string;
  iterations: number;
  totalTime: number;
  averageTime: number;
  minTime: number;
  maxTime: number;
  percentiles: {
    p50: number;
    p90: number;
    p95: number;
    p99: number;
  };
}

/**
 * Run a performance test and collect metrics
 */
export async function runPerformanceTest(
  test: PerformanceTest
): Promise<PerformanceTestResult> {
  const { name, fn, iterations = 100, warmup = 10 } = test;
  const times: number[] = [];

  console.log(`Running performance test: ${name}`);
  console.log(`Warmup iterations: ${warmup}`);
  console.log(`Test iterations: ${iterations}`);

  // Warmup runs
  for (let i = 0; i < warmup; i++) {
    await fn();
  }

  // Actual test runs
  for (let i = 0; i < iterations; i++) {
    const start = performance.now();
    await fn();
    const end = performance.now();
    times.push(end - start);
  }

  // Calculate statistics
  times.sort((a, b) => a - b);
  const totalTime = times.reduce((sum, time) => sum + time, 0);
  const averageTime = totalTime / iterations;
  const minTime = times[0];
  const maxTime = times[times.length - 1];

  const percentiles = {
    p50: times[Math.floor(iterations * 0.5)],
    p90: times[Math.floor(iterations * 0.9)],
    p95: times[Math.floor(iterations * 0.95)],
    p99: times[Math.floor(iterations * 0.99)],
  };

  return {
    name,
    iterations,
    totalTime,
    averageTime,
    minTime,
    maxTime,
    percentiles,
  };
}

/**
 * Run multiple performance tests and compare results
 */
export async function runPerformanceComparison(
  tests: PerformanceTest[]
): Promise<PerformanceTestResult[]> {
  const results: PerformanceTestResult[] = [];

  for (const test of tests) {
    const result = await runPerformanceTest(test);
    results.push(result);
  }

  // Print comparison table
  console.table(
    results.map(r => ({
      Name: r.name,
      'Avg (ms)': r.averageTime.toFixed(2),
      'Min (ms)': r.minTime.toFixed(2),
      'Max (ms)': r.maxTime.toFixed(2),
      'P50 (ms)': r.percentiles.p50.toFixed(2),
      'P90 (ms)': r.percentiles.p90.toFixed(2),
      'P95 (ms)': r.percentiles.p95.toFixed(2),
    }))
  );

  return results;
}

/**
 * Measure React component render performance
 */
export function measureComponentRender<P extends {}>(
  Component: React.ComponentType<P>,
  props: P,
  iterations = 100
): PerformanceTestResult {
  // This would need to be run in a test environment with React
  // For now, return a placeholder
  console.warn('Component render testing requires a React test environment');
  return {
    name: Component.displayName || Component.name || 'Unknown',
    iterations: 0,
    totalTime: 0,
    averageTime: 0,
    minTime: 0,
    maxTime: 0,
    percentiles: {
      p50: 0,
      p90: 0,
      p95: 0,
      p99: 0,
    },
  };
}

/**
 * Profile a function and get detailed performance breakdown
 */
export async function profileFunction<T>(
  name: string,
  fn: () => T | Promise<T>
): Promise<{ result: T; profile: PerformanceProfile }> {
  const profile: PerformanceProfile = {
    name,
    startTime: performance.now(),
    endTime: 0,
    duration: 0,
    memoryBefore: getMemoryUsage(),
    memoryAfter: null,
    markers: [],
  };

  const result = await fn();

  profile.endTime = performance.now();
  profile.duration = profile.endTime - profile.startTime;
  profile.memoryAfter = getMemoryUsage();

  logProfile(profile);

  return { result, profile };
}

export interface PerformanceProfile {
  name: string;
  startTime: number;
  endTime: number;
  duration: number;
  memoryBefore: MemoryUsage | null;
  memoryAfter: MemoryUsage | null;
  markers: Array<{ name: string; time: number }>;
}

export interface MemoryUsage {
  usedJSHeapSize: number;
  totalJSHeapSize: number;
  jsHeapSizeLimit: number;
}

function getMemoryUsage(): MemoryUsage | null {
  if ('memory' in performance) {
    const memory = (performance as any).memory;
    return {
      usedJSHeapSize: memory.usedJSHeapSize,
      totalJSHeapSize: memory.totalJSHeapSize,
      jsHeapSizeLimit: memory.jsHeapSizeLimit,
    };
  }
  return null;
}

function logProfile(profile: PerformanceProfile) {
  console.group(`[Profile] ${profile.name}`);
  console.log(`Duration: ${profile.duration.toFixed(2)}ms`);
  
  if (profile.memoryBefore && profile.memoryAfter) {
    const memoryDelta = profile.memoryAfter.usedJSHeapSize - profile.memoryBefore.usedJSHeapSize;
    console.log(`Memory delta: ${(memoryDelta / 1024 / 1024).toFixed(2)}MB`);
  }
  
  if (profile.markers.length > 0) {
    console.log('Markers:');
    profile.markers.forEach(marker => {
      console.log(`  ${marker.name}: ${(marker.time - profile.startTime).toFixed(2)}ms`);
    });
  }
  
  console.groupEnd();
}

/**
 * Create a performance benchmark suite
 */
export class PerformanceBenchmark {
  private tests: Map<string, PerformanceTest> = new Map();
  
  add(name: string, fn: () => void | Promise<void>, options?: Partial<PerformanceTest>) {
    this.tests.set(name, {
      name,
      fn,
      ...options,
    });
    return this;
  }
  
  async run(): Promise<Map<string, PerformanceTestResult>> {
    const results = new Map<string, PerformanceTestResult>();
    
    const entries = Array.from(this.tests.entries());
    for (const [name, test] of entries) {
      const result = await runPerformanceTest(test);
      results.set(name, result);
    }
    
    return results;
  }
  
  async compare(baseline: string): Promise<void> {
    const results = await this.run();
    const baselineResult = results.get(baseline);
    
    if (!baselineResult) {
      console.error(`Baseline test "${baseline}" not found`);
      return;
    }
    
    console.log(`\nPerformance comparison (baseline: ${baseline})`);
    console.log('='.repeat(60));
    
    const resultEntries = Array.from(results.entries());
    for (const [name, result] of resultEntries) {
      if (name === baseline) continue;
      
      const speedup = baselineResult.averageTime / result.averageTime;
      const faster = speedup > 1;
      
      console.log(
        `${name}: ${faster ? speedup.toFixed(2) + 'x faster' : (1/speedup).toFixed(2) + 'x slower'}`
      );
    }
  }
}