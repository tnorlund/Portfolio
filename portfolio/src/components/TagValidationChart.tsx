import React, { useState, useEffect, useRef } from "react";
import { useInView } from "react-intersection-observer";
import { TagValidationStatsResponse, TagStats } from "../interfaces";
import { fetchTagValidationStats } from "../api";
import "./TagValidationChart.css"; // We'll put our animation CSS in here

interface ChartRowProps {
  tag: string;
  stats: TagStats;
  barWidth: number;
  onVisible: () => void;
  xScale: (v: number) => number;
}

const ChartRow: React.FC<ChartRowProps> = ({
  tag,
  stats,
  barWidth,
  onVisible,
  xScale,
}) => {
  const [ref, inView] = useInView({
    triggerOnce: true,
    threshold: 0.2,
  });

  useEffect(() => {
    if (inView) {
      onVisible();
    }
  }, [inView, onVisible]);

  // Calculate positions for each segment
  const positions = {
    validatedTrueHumanFalse: xScale(stats.validated_true_human_false),
    validatedTrueHumanTrue: xScale(stats.validated_true_human_true),
    validatedFalseHumanFalse: xScale(stats.validated_false_human_false),
    validatedFalseHumanTrue: xScale(stats.validated_false_human_true),
    validatedNoneHumanTrue: xScale(stats.validated_none_human_true),
  };

  // Calculate total width for GPT sections
  const gptValidWidth = positions.validatedTrueHumanFalse + positions.validatedTrueHumanTrue;
  const gptInvalidWidth = positions.validatedFalseHumanFalse + positions.validatedFalseHumanTrue;

  let currentX = 4; // Starting position

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
          {/* Human Valid (Green) sections */}
          <path
            d={`
              M ${currentX + positions.validatedTrueHumanFalse} 12
              H ${currentX + gptValidWidth}
              V 24
              H ${currentX + positions.validatedTrueHumanFalse}
              V 12
            `}
            fill="var(--color-green)"
            stroke="var(--color-green)"
            strokeWidth={2}
            vectorEffect="non-scaling-stroke"
          />

          {/* Human Invalid (Red) section */}
          {(() => {
            const redX = currentX + gptValidWidth;
            return (
              <path
                d={`
                  M ${redX + positions.validatedFalseHumanFalse} 12
                  H ${redX + gptInvalidWidth}
                  V 24
                  H ${redX + positions.validatedFalseHumanFalse}
                  V 12
                `}
                fill="var(--color-red)"
                stroke="var(--color-red)"
                strokeWidth={2}
                vectorEffect="non-scaling-stroke"
              />
            );
          })()}

          {/* GPT Valid section on top */}
          <path
            d={`
              M ${currentX + 4} 0
              H ${currentX + gptValidWidth}
              V 12
              H ${currentX}
              V 4
              a 4 4 0 0 1 4 -4
            `}
            fill="var(--text-color)"
            stroke="var(--text-color)"
            strokeWidth={2}
            vectorEffect="non-scaling-stroke"
          />

          {/* GPT Invalid section on top */}
          {(() => {
            currentX += gptValidWidth;
            return (
              <path
                d={`
                  M ${currentX} 0
                  H ${currentX + gptInvalidWidth - 4}
                  a 4 4 0 0 1 4 4
                  V 12
                  H ${currentX}
                  V 0
                `}
                fill="var(--background-color)"
                stroke="var(--text-color)"
                strokeWidth={2}
                vectorEffect="non-scaling-stroke"
              />
            );
          })()}
        </svg>
        <div className="total-count">{stats.total}</div>
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
  const [visibleCount, setVisibleCount] = useState(0);
  const [legendVisible, setLegendVisible] = useState(false);

  useEffect(() => {
    const handleResize = () => {
      if (containerRef.current?.parentElement) {
        const parentWidth = containerRef.current.parentElement.clientWidth;
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
  }, []);

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const data = await fetchTagValidationStats();
        setStats(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : "An error occurred");
      } finally {
        setLoading(false);
      }
    };

    fetchStats();
  }, []);

  useEffect(() => {
    if (stats && visibleCount === Object.keys(stats.tag_stats).length) {
      setLegendVisible(true);
    }
  }, [stats, visibleCount]);

  const handleRowVisible = React.useCallback(() => {
    setVisibleCount((count) => count + 1);
  }, []);

  if (loading) return (
    <div style={{
      display: "flex",
      justifyContent: "center",
      alignItems: "center",
      margin: "16px 0",
      height: "421px",
    }}>
      Loading tag validation statistics...
    </div>
  );

  if (error) return (
    <div style={{
      display: "flex",
      justifyContent: "center",
      alignItems: "center",
      margin: "16px 0",
      height: "421px",
    }}>
      Error loading tag validation stats: {error}
    </div>
  );

  if (!stats) return (
    <div style={{
      display: "flex",
      justifyContent: "center",
      alignItems: "center",
      margin: "16px 0",
      height: "421px",
    }}>
      No data available
    </div>
  );

  const chartData = Object.entries(stats.tag_stats)
    .sort((a, b) => b[1].total - a[1].total);

  const maxValue = Math.max(...chartData.map(([_, stats]) => (
    stats.validated_true_human_false + 
    stats.validated_true_human_true + 
    stats.validated_false_human_false + 
    stats.validated_false_human_true +
    stats.validated_none_human_true
  )));
  const barWidth = dimensions.width - 180;
  const xScale = (value: number) => (value / maxValue) * barWidth;

  return (
    <div ref={containerRef} className="tag-validation-container">
      <div className="chart-content">
        {chartData.map(([tag, tagStats]) => (
          <ChartRow
            key={tag}
            tag={tag}
            stats={tagStats}
            barWidth={barWidth}
            xScale={xScale}
            onVisible={handleRowVisible}
          />
        ))}
      </div>

      <div className={`chart-legend ${legendVisible ? "show" : ""}`}>
        <div className="legend-item">
          <div className="legend-swatch filled" />
          <span>GPT Valid</span>
        </div>
        <div className="legend-item">
          <div className="legend-swatch outlined" />
          <span>GPT Invalid</span>
        </div>
        <div className="legend-item">
          <div className="legend-swatch" style={{ backgroundColor: "var(--color-green)" }} />
          <span>Human Valid</span>
        </div>
        <div className="legend-item">
          <div className="legend-swatch" style={{ backgroundColor: "var(--color-red)" }} />
          <span>Human Invalid</span>
        </div>
      </div>
    </div>
  );
};

export default TagValidationChart;
