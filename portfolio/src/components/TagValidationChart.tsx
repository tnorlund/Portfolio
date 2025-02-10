import React, { useState, useEffect, useRef } from "react";
import { useInView } from "react-intersection-observer";
import { TagValidationStatsResponse } from "../interfaces";
import { fetchTagValidationStats } from "../api";
import "./TagValidationChart.css"; // We'll put our animation CSS in here

interface ChartRowProps {
  tag: string;
  valid: number;
  invalid: number;
  total: number;
  barWidth: number;
  onVisible: () => void; // Notifies parent when it becomes visible
  xScale: (v: number) => number;
}

const ChartRow: React.FC<ChartRowProps> = ({
  tag,
  valid,
  invalid,
  total,
  barWidth,
  onVisible,
  xScale,
}) => {
  const [ref, inView] = useInView({
    triggerOnce: true,
    threshold: 0.2,
  });

  // Add onVisible to dependency array and memoize the callback
  useEffect(() => {
    if (inView) {
      onVisible();
    }
  }, [inView, onVisible]);

  return (
    <div ref={ref} className={`chart-row ${inView ? "visible" : ""}`}>
      <div className="tag-label">{tag}</div>
      <div className="bar-container" style={{ width: "100%" }}>
        <svg
        width="100%"
        height={30}
        preserveAspectRatio="none"
        viewBox={`-4 -4 ${barWidth + 16} 30`}
        >
          {/* Valid portion - left corners rounded */}
          <path
            d={`
              M 4 0
              H ${4 + xScale(valid)}
              V 24
              H 4
              a 4 4 0 0 1 -4 -4
              V 4
              a 4 4 0 0 1 4 -4
            `}
            fill="var(--text-color)"
            stroke="var(--text-color)"
            strokeWidth={2}
            vectorEffect="non-scaling-stroke"
          />
          {/* Invalid portion - right corners rounded */}
          <path
            d={`
              M ${4 + xScale(valid)} 0
              H ${4 + xScale(valid) + xScale(invalid)}
              a 4 4 0 0 1 4 4
              v 16
              a 4 4 0 0 1 -4 4
              H ${4 + xScale(valid)}
              V 0
            `}
            fill="none"
            stroke="var(--text-color)"
            strokeWidth={2}
            vectorEffect="non-scaling-stroke"
          />
        </svg>
        <div className="total-count">{total}</div>
      </div>
    </div>
  );
};

const TagValidationChart: React.FC = () => {
  const [stats, setStats] = useState<TagValidationStatsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 600 });

  // Tracks how many rows are visible
  const [visibleCount, setVisibleCount] = useState(0);
  const [legendVisible, setLegendVisible] = useState(false);

  useEffect(() => {
    const handleResize = () => {
      if (containerRef.current?.parentElement) {
        const parentWidth = containerRef.current.parentElement.clientWidth;
        // Only update if width actually changed
        setDimensions((prev) => {
          if (prev.width !== parentWidth) {
            return { width: parentWidth };
          }
          return prev;
        });
      }
    };

    handleResize();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []); // Empty dependency array is fine here since we're using refs

  useEffect(() => {
    const fetchStatsData = async () => {
      try {
        const data = await fetchTagValidationStats();
        setStats(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : "An error occurred");
      } finally {
        setLoading(false);
      }
    };
    fetchStatsData();
  }, []);

  // If we know how many rows we have total, once visibleCount matches, show legend
  useEffect(() => {
    if (stats && visibleCount === Object.keys(stats.tag_stats).length) {
      setLegendVisible(true);
    }
  }, [stats, visibleCount]);

  // Memoize the onVisible callback
  const handleRowVisible = React.useCallback(() => {
    setVisibleCount((count) => count + 1);
  }, []);

  if (loading)
    return (
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          margin: "16px 0",
          height: "421px",
        }}
      >
        Loading tag validation statistics...
      </div>
    );

  if (error)
    return (
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          margin: "16px 0",
          height: "421px",
        }}
      >
        Error loading tag validation stats: {error}
      </div>
    );

  if (!stats)
    return (
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          margin: "16px 0",
          height: "421px",
        }}
      >
        No data available
      </div>
    );

  // Prepare data
  const chartData = Object.entries(stats.tag_stats)
    .map(([tag, detail]) => ({
      tag,
      valid: detail.valid,
      invalid: detail.invalid,
      total: detail.total,
    }))
    .sort((a, b) => b.total - a.total);

  const maxValue = Math.max(...chartData.map((d) => d.total));
  const barWidth = dimensions.width - 180;
  const xScale = (value: number) => (value / maxValue) * barWidth;

  return (
    <div ref={containerRef} className="tag-validation-container">
      <div className="chart-content">
        {chartData.map((d) => (
          <ChartRow
            key={d.tag}
            tag={d.tag}
            valid={d.valid}
            invalid={d.invalid}
            total={d.total}
            barWidth={barWidth}
            xScale={xScale}
            onVisible={handleRowVisible} // Use memoized callback
          />
        ))}
      </div>

      {/* Only fade in the legend once all rows have animated in */}
      <div className={`chart-legend ${legendVisible ? "show" : ""}`}>
        <div className="legend-item">
          <div className="legend-swatch filled" />
          <span>GPT Valid</span>
        </div>
        <div className="legend-item">
          <div className="legend-swatch outlined" />
          <span>GPT Invalid</span>
        </div>
      </div>
    </div>
  );
};

export default TagValidationChart;
