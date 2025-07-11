import React from 'react';
import { render, screen } from '@testing-library/react';
import { act } from 'react';

// Mock component for testing render performance
const ExpensiveComponent: React.FC<{ items: number }> = ({ items }) => {
  // Simulate expensive computation
  const expensiveData = React.useMemo(() => {
    return Array.from({ length: items }, (_, i) => ({
      id: i,
      value: Math.random(),
    }));
  }, [items]);

  return (
    <div>
      {expensiveData.map(item => (
        <div key={item.id} data-testid="item">
          {item.value}
        </div>
      ))}
    </div>
  );
};

describe('Rendering Performance Tests', () => {
  beforeEach(() => {
    // Clear any performance marks
    performance.clearMarks();
    performance.clearMeasures();
  });

  test('should render large lists efficiently', () => {
    const itemCount = 1000;
    
    performance.mark('render-start');
    
    render(<ExpensiveComponent items={itemCount} />);
    
    performance.mark('render-end');
    performance.measure('render-duration', 'render-start', 'render-end');
    
    const measure = performance.getEntriesByName('render-duration')[0];
    const renderTime = measure.duration;
    
    console.log(`Rendered ${itemCount} items in ${renderTime.toFixed(2)}ms`);
    
    // Should render 1000 items in under 100ms
    expect(renderTime).toBeLessThan(100);
    
    // Verify all items rendered
    const items = screen.getAllByTestId('item');
    expect(items).toHaveLength(itemCount);
  });

  test('should handle re-renders efficiently', () => {
    const { rerender } = render(<ExpensiveComponent items={100} />);
    
    const measurements: number[] = [];
    
    // Measure multiple re-renders
    for (let i = 0; i < 10; i++) {
      performance.mark(`rerender-start-${i}`);
      
      act(() => {
        rerender(<ExpensiveComponent items={100 + i} />);
      });
      
      performance.mark(`rerender-end-${i}`);
      performance.measure(`rerender-${i}`, `rerender-start-${i}`, `rerender-end-${i}`);
      
      const measure = performance.getEntriesByName(`rerender-${i}`)[0];
      measurements.push(measure.duration);
    }
    
    const avgRerenderTime = measurements.reduce((a, b) => a + b, 0) / measurements.length;
    console.log(`Average re-render time: ${avgRerenderTime.toFixed(2)}ms`);
    
    // Re-renders should be fast (under 20ms average)
    expect(avgRerenderTime).toBeLessThan(20);
    
    // Re-render times should be consistent (low variance)
    const variance = measurements.reduce((sum, time) => 
      sum + Math.pow(time - avgRerenderTime, 2), 0) / measurements.length;
    const stdDev = Math.sqrt(variance);
    
    console.log(`Standard deviation: ${stdDev.toFixed(2)}ms`);
    expect(stdDev).toBeLessThan(10); // Low variance
  });

  test('should memoize expensive computations', () => {
    // Track computation calls directly instead of mocking native API
    let computationCallCount = 0;
    const computationResults: number[] = [];
    
    const MemoizedComponent: React.FC<{ value: number; unrelated: string }> = 
      React.memo(({ value, unrelated }) => {
        // This computation should only run when 'value' changes
        const expensiveResult = React.useMemo(() => {
          computationCallCount++;
          
          // Simulate expensive computation
          let result = 0;
          for (let i = 0; i < 100000; i++) { // Reduced iterations for test performance
            result += Math.sqrt(i * value);
          }
          
          computationResults.push(result);
          return result;
        }, [value]);
        
        return (
          <div>
            <span data-testid="result">{expensiveResult.toFixed(2)}</span>
            <span data-testid="unrelated">{unrelated}</span>
          </div>
        );
      });
    
    MemoizedComponent.displayName = 'MemoizedComponent';
    
    // Initial render
    const { rerender, getByTestId } = render(
      <MemoizedComponent value={5} unrelated="initial" />
    );
    
    // Should have computed once
    expect(computationCallCount).toBe(1);
    const initialResult = getByTestId('result').textContent;
    expect(computationResults).toHaveLength(1);
    
    // Re-render with same value but different unrelated prop
    rerender(<MemoizedComponent value={5} unrelated="updated" />);
    
    // Computation should NOT have run again (memoization working)
    expect(computationCallCount).toBe(1);
    expect(computationResults).toHaveLength(1);
    expect(getByTestId('result').textContent).toBe(initialResult);
    expect(getByTestId('unrelated').textContent).toBe('updated');
    
    // Re-render with different value
    rerender(<MemoizedComponent value={10} unrelated="updated" />);
    
    // Computation should have run again (dependency changed)
    expect(computationCallCount).toBe(2);
    expect(computationResults).toHaveLength(2);
    expect(getByTestId('result').textContent).not.toBe(initialResult);
    
    console.log(`Computation ran ${computationCallCount} times (expected: 2)`);
    console.log(`Results: ${computationResults.map(r => r.toFixed(2)).join(', ')}`);
  });

  test('should batch state updates efficiently', async () => {
    const BatchUpdateComponent: React.FC = () => {
      const [count1, setCount1] = React.useState(0);
      const [count2, setCount2] = React.useState(0);
      const [count3, setCount3] = React.useState(0);
      
      const handleClick = () => {
        // These should be batched in React 18+
        setCount1(c => c + 1);
        setCount2(c => c + 1);
        setCount3(c => c + 1);
      };
      
      // Count renders
      React.useEffect(() => {
        performance.mark('component-render');
      });
      
      return (
        <div>
          <button onClick={handleClick}>Update</button>
          <div data-testid="counts">
            {count1}-{count2}-{count3}
          </div>
        </div>
      );
    };
    
    render(<BatchUpdateComponent />);
    
    // Clear marks
    performance.clearMarks();
    
    // Trigger batch update
    const button = screen.getByRole('button');
    
    act(() => {
      button.click();
    });
    
    // Should only render once despite 3 state updates
    const renderMarks = performance.getEntriesByName('component-render');
    expect(renderMarks).toHaveLength(1);
    
    // Verify all states updated
    expect(screen.getByTestId('counts')).toHaveTextContent('1-1-1');
  });
});

// Performance benchmark utilities
export function measureRenderTime(component: React.ReactElement): number {
  performance.mark('render-start');
  render(component);
  performance.mark('render-end');
  performance.measure('render', 'render-start', 'render-end');
  
  const measure = performance.getEntriesByName('render')[0];
  return measure.duration;
}

export function benchmarkComponent(
  name: string,
  component: React.ReactElement,
  iterations = 100
): void {
  const times: number[] = [];
  
  for (let i = 0; i < iterations; i++) {
    const { unmount } = render(component);
    const time = measureRenderTime(component);
    times.push(time);
    unmount();
  }
  
  const avg = times.reduce((a, b) => a + b, 0) / times.length;
  const min = Math.min(...times);
  const max = Math.max(...times);
  
  console.log(`
Benchmark: ${name}
Iterations: ${iterations}
Average: ${avg.toFixed(2)}ms
Min: ${min.toFixed(2)}ms
Max: ${max.toFixed(2)}ms
  `);
}