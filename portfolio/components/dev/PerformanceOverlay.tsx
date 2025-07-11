import React, { useState, useEffect, useCallback } from 'react';
import { usePerformanceMonitor } from '../../hooks/usePerformanceMonitor';
import { PerformanceMetrics } from '../../utils/performance/monitor';

interface PerformanceOverlayProps {
  enabled?: boolean;
  position?: 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right';
}

const PerformanceOverlay: React.FC<PerformanceOverlayProps> = ({
  enabled = process.env.NODE_ENV === 'development',
  position = 'bottom-right',
}) => {
  const { metrics } = usePerformanceMonitor({ trackRender: false });
  const [isMinimized, setIsMinimized] = useState(false);
  const [fps, setFps] = useState(0);

  // Track FPS
  useEffect(() => {
    if (!enabled) return;

    let frameCount = 0;
    let lastTime = performance.now();

    const calculateFPS = () => {
      frameCount++;
      const currentTime = performance.now();
      
      if (currentTime >= lastTime + 1000) {
        setFps(Math.round((frameCount * 1000) / (currentTime - lastTime)));
        frameCount = 0;
        lastTime = currentTime;
      }

      requestAnimationFrame(calculateFPS);
    };

    const rafId = requestAnimationFrame(calculateFPS);
    return () => cancelAnimationFrame(rafId);
  }, [enabled]);

  const getMemoryUsage = useCallback(() => {
    if (!metrics.usedJSHeapSize || !metrics.jsHeapSizeLimit) return null;
    const used = metrics.usedJSHeapSize / (1024 * 1024); // Convert to MB
    const limit = metrics.jsHeapSizeLimit / (1024 * 1024);
    const percentage = (metrics.usedJSHeapSize / metrics.jsHeapSizeLimit) * 100;
    return { used, limit, percentage };
  }, [metrics]);

  const getVitalStatus = (value: number | undefined, good: number, needs: number) => {
    if (value === undefined) return 'gray';
    if (value <= good) return '#0cce6b';
    if (value <= needs) return '#ffa400';
    return '#ff4e42';
  };

  const formatMs = (ms: number | undefined) => {
    if (ms === undefined) return 'N/A';
    return `${Math.round(ms)}ms`;
  };

  const formatCLS = (cls: number | undefined) => {
    if (cls === undefined) return 'N/A';
    return cls.toFixed(3);
  };

  if (!enabled) return null;

  const memoryUsage = getMemoryUsage();

  const positionStyles = {
    'top-left': { top: 10, left: 10 },
    'top-right': { top: 10, right: 10 },
    'bottom-left': { bottom: 10, left: 10 },
    'bottom-right': { bottom: 10, right: 10 },
  };

  return (
    <div
      style={{
        position: 'fixed',
        ...positionStyles[position],
        backgroundColor: 'rgba(0, 0, 0, 0.9)',
        color: 'white',
        padding: isMinimized ? '8px 12px' : '12px',
        borderRadius: '8px',
        fontSize: '12px',
        fontFamily: 'monospace',
        zIndex: 99999,
        minWidth: isMinimized ? 'auto' : '280px',
        boxShadow: '0 4px 12px rgba(0, 0, 0, 0.3)',
        transition: 'all 0.2s ease',
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: isMinimized ? 0 : '8px',
          cursor: 'pointer',
        }}
        onClick={() => setIsMinimized(!isMinimized)}
      >
        <span style={{ fontWeight: 'bold' }}>
          {isMinimized ? '⚡' : '⚡ Performance'}
        </span>
        <span>{isMinimized ? '▼' : '▲'}</span>
      </div>

      {!isMinimized && (
        <>
          {/* FPS Counter */}
          <div style={{ marginBottom: '8px', borderBottom: '1px solid rgba(255, 255, 255, 0.2)', paddingBottom: '8px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>FPS:</span>
              <span style={{ color: fps >= 55 ? '#0cce6b' : fps >= 30 ? '#ffa400' : '#ff4e42' }}>
                {fps}
              </span>
            </div>
          </div>

          {/* Core Web Vitals */}
          <div style={{ marginBottom: '8px' }}>
            <div style={{ fontWeight: 'bold', marginBottom: '4px' }}>Core Web Vitals</div>
            
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '2px' }}>
              <span>LCP:</span>
              <span style={{ color: getVitalStatus(metrics.lcp, 2500, 4000) }}>
                {formatMs(metrics.lcp)}
              </span>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '2px' }}>
              <span>FID:</span>
              <span style={{ color: getVitalStatus(metrics.fid, 100, 300) }}>
                {formatMs(metrics.fid)}
              </span>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '2px' }}>
              <span>CLS:</span>
              <span style={{ color: getVitalStatus(metrics.cls, 0.1, 0.25) }}>
                {formatCLS(metrics.cls)}
              </span>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '2px' }}>
              <span>FCP:</span>
              <span style={{ color: getVitalStatus(metrics.fcp, 1800, 3000) }}>
                {formatMs(metrics.fcp)}
              </span>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>TTFB:</span>
              <span style={{ color: getVitalStatus(metrics.ttfb, 800, 1800) }}>
                {formatMs(metrics.ttfb)}
              </span>
            </div>
          </div>

          {/* Memory Usage */}
          {memoryUsage && (
            <div style={{ marginBottom: '8px', borderTop: '1px solid rgba(255, 255, 255, 0.2)', paddingTop: '8px' }}>
              <div style={{ fontWeight: 'bold', marginBottom: '4px' }}>Memory</div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '2px' }}>
                <span>Heap:</span>
                <span>{memoryUsage.used.toFixed(1)} / {memoryUsage.limit.toFixed(1)} MB</span>
              </div>
              <div
                style={{
                  width: '100%',
                  height: '4px',
                  backgroundColor: 'rgba(255, 255, 255, 0.2)',
                  borderRadius: '2px',
                  overflow: 'hidden',
                }}
              >
                <div
                  style={{
                    width: `${memoryUsage.percentage}%`,
                    height: '100%',
                    backgroundColor: memoryUsage.percentage > 90 ? '#ff4e42' : memoryUsage.percentage > 70 ? '#ffa400' : '#0cce6b',
                    transition: 'width 0.3s ease',
                  }}
                />
              </div>
            </div>
          )}

          {/* Component Render Times */}
          {metrics.componentRenderTime && Object.keys(metrics.componentRenderTime).length > 0 && (
            <div style={{ borderTop: '1px solid rgba(255, 255, 255, 0.2)', paddingTop: '8px' }}>
              <div style={{ fontWeight: 'bold', marginBottom: '4px' }}>Component Renders</div>
              {Object.entries(metrics.componentRenderTime)
                .slice(-5) // Show last 5 components
                .map(([name, times]) => {
                  const avgTime = times.reduce((a, b) => a + b, 0) / times.length;
                  return (
                    <div key={name} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '2px', fontSize: '11px' }}>
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '150px' }}>
                        {name}:
                      </span>
                      <span style={{ color: avgTime < 16 ? '#0cce6b' : avgTime < 50 ? '#ffa400' : '#ff4e42' }}>
                        {avgTime.toFixed(1)}ms ({times.length}x)
                      </span>
                    </div>
                  );
                })}
            </div>
          )}

          {/* API Call Times */}
          {metrics.apiCallDuration && Object.keys(metrics.apiCallDuration).length > 0 && (
            <div style={{ borderTop: '1px solid rgba(255, 255, 255, 0.2)', paddingTop: '8px', marginTop: '8px' }}>
              <div style={{ fontWeight: 'bold', marginBottom: '4px' }}>API Calls</div>
              {Object.entries(metrics.apiCallDuration)
                .slice(-3) // Show last 3 API calls
                .map(([endpoint, times]) => {
                  const lastTime = times[times.length - 1];
                  return (
                    <div key={endpoint} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '2px', fontSize: '11px' }}>
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '150px' }}>
                        {endpoint}:
                      </span>
                      <span style={{ color: lastTime < 200 ? '#0cce6b' : lastTime < 500 ? '#ffa400' : '#ff4e42' }}>
                        {lastTime.toFixed(0)}ms
                      </span>
                    </div>
                  );
                })}
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default PerformanceOverlay;