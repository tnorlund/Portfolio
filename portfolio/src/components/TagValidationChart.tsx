import React, { useState, useEffect, useRef } from 'react';
import { TagValidationStatsResponse } from '../interfaces';
import { fetchTagValidationStats } from '../api';
import './TagValidationChart.css';

interface TagValidationChartProps {}

const TagValidationChart: React.FC<TagValidationChartProps> = () => {
  const [stats, setStats] = useState<TagValidationStatsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 600 });

  useEffect(() => {
    const handleResize = () => {
      if (containerRef.current?.parentElement) {
        const parentWidth = containerRef.current.parentElement.clientWidth;
        setDimensions({ width: parentWidth });
      }
    };

    handleResize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const data = await fetchTagValidationStats();
        setStats(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'An error occurred');
        console.error('Error fetching tag stats:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchStats();
  }, []);

  if (loading) return <div>Loading tag validation statistics...</div>;
  if (error) return <div>Error loading tag validation stats: {error}</div>;
  if (!stats) return <div>No data available</div>;

  const chartData = Object.entries(stats.tag_stats)
    .map(([tag, stats]) => ({
      tag,
      valid: stats.valid,
      invalid: stats.invalid,
      total: stats.total
    }))
    .sort((a, b) => b.total - a.total);

  const maxValue = Math.max(...chartData.map(d => d.total));
  const barWidth = dimensions.width - 180; // Just account for label width and total count
  const xScale = (value: number) => (value / maxValue) * barWidth;

  return (
    <div ref={containerRef} className="tag-validation-container">
      <div className="chart-content">
        {chartData.map((d) => (
          <div key={d.tag} className="chart-row">
            <div className="tag-label">{d.tag}</div>
            <div className="bar-container">
              <svg 
                width="100%"
                height={28}
                viewBox={`-2 -2 ${barWidth + 4} 28`}
                preserveAspectRatio="none"
              >
                {/* Valid portion - round only the left corners */}
                <path
                  d={`
                    M ${4} 0
                    h ${xScale(d.valid) - 4}
                    v 24
                    h ${-xScale(d.valid) + 4}
                    a 4 4 0 0 1 -4 -4
                    v -16
                    a 4 4 0 0 1 4 -4
                  `}
                  fill="var(--text-color)"
                  stroke="var(--text-color)"
                  strokeWidth={2}
                />
                {/* Invalid portion - round only the right corners */}
                <path
                  d={`
                    M ${xScale(d.valid)} 0
                    h ${xScale(d.invalid) - 4}
                    a 4 4 0 0 1 4 4
                    v 16
                    a 4 4 0 0 1 -4 4
                    h ${-xScale(d.invalid) + 4}
                    v -24
                  `}
                  fill="none"
                  stroke="var(--text-color)"
                  strokeWidth={2}
                />
              </svg>
              <div className="total-count">{d.total}</div>
            </div>
          </div>
        ))}
      </div>

      <div className="chart-legend">
        <div className="legend-item">
          <div className="legend-swatch filled"></div>
          <span>GPT Valid</span>
        </div>
        <div className="legend-item">
          <div className="legend-swatch outlined"></div>
          <span>GPT Invalid</span>
        </div>
      </div>
    </div>
  );
};

export default TagValidationChart; 