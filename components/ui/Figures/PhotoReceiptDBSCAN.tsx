import React, { useEffect, useState } from "react";
import { type Point as ApiPoint } from "../../../types/api";
import { useTransition, animated } from "@react-spring/web";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import { getBestImageUrl } from "../../../utils/imageFormat";
import useImageDetails from "../../../hooks/useImageDetails";
import useReceiptClustering, { calculateEpsilonFromLines } from "../../../hooks/useReceiptClustering";

const isDevelopment = process.env.NODE_ENV === "development";

const colors = [
  "var(--color-red)",
  "var(--color-blue)",
  "var(--color-green)",
  "var(--color-purple)",
  "var(--color-orange)",
  "var(--color-cyan)",
  "var(--color-pink)",
  "var(--color-yellow)",
];

interface DBSCANControlsProps {
  epsilon: number;
  minPoints: number;
  onEpsilonChange: (value: number) => void;
  onMinPointsChange: (value: number) => void;
}

const DBSCANControls: React.FC<DBSCANControlsProps> = ({
  epsilon,
  minPoints,
  onEpsilonChange,
  onMinPointsChange,
}) => {
  return (
    <div style={{ marginBottom: "1rem", textAlign: "center" }}>
      <div style={{ marginBottom: "0.5rem" }}>
        <label style={{ marginRight: "0.5rem" }}>
          Epsilon: {epsilon.toFixed(3)}
        </label>
        <input
          type="range"
          min="0.01"
          max="0.2"
          step="0.001"
          value={epsilon}
          onChange={(e) => onEpsilonChange(parseFloat(e.target.value))}
          style={{ width: "200px" }}
        />
      </div>
      <div>
        <label style={{ marginRight: "0.5rem" }}>
          Min Points: {minPoints}
        </label>
        <input
          type="range"
          min="1"
          max="10"
          step="1"
          value={minPoints}
          onChange={(e) => onMinPointsChange(parseInt(e.target.value))}
          style={{ width: "200px" }}
        />
      </div>
    </div>
  );
};

const PhotoReceiptDBSCAN: React.FC = () => {
  const { imageDetails, formatSupport, error } = useImageDetails("PHOTO");
  const [isClient, setIsClient] = useState(false);
  const [resetKey, setResetKey] = useState(0);
  const [ref, inView] = useOptimizedInView({ threshold: 0.3 });
  
  const lines = imageDetails?.lines ?? [];
  const calculatedEpsilon = calculateEpsilonFromLines(lines);
  
  const [epsilon, setEpsilon] = useState(calculatedEpsilon);
  const [minPoints, setMinPoints] = useState(3);
  
  // Update epsilon when lines change
  useEffect(() => {
    if (lines.length > 0) {
      setEpsilon(calculateEpsilonFromLines(lines));
    }
  }, [lines]);

  useEffect(() => {
    setIsClient(true);
  }, []);

  const defaultSvgWidth = 400;
  const defaultSvgHeight = 565.806;
  
  const { clusters, noiseLines } = useReceiptClustering(lines, {
    epsilon,
    minPoints,
    useNormalization: true,
  });

  const lineTransitions = useTransition(inView ? lines : [], {
    keys: (line) => `${resetKey}-${line.line_id}`,
    from: { opacity: 0, transform: "scale(0.8)" },
    enter: (item, index) => ({
      opacity: 0.3,
      transform: "scale(1)",
      delay: index * 20,
    }),
    config: { duration: 600 },
  });

  const clustersWithKeys = inView ? clusters.map((cluster, index) => ({
    ...cluster,
    key: `${resetKey}-cluster-${index}`
  })) : [];
  
  const clusterTransitions = useTransition(clustersWithKeys, {
    from: { opacity: 0 },
    enter: (item, index) => ({
      opacity: 1,
      delay: lines.length * 20 + index * 100,
    }),
    config: { duration: 800 },
  });

  const firstImage = imageDetails?.image;
  const cdnUrl =
    firstImage && formatSupport && isClient
      ? getBestImageUrl(firstImage, formatSupport)
      : firstImage
      ? `${
          isDevelopment
            ? "https://dev.tylernorlund.com"
            : "https://www.tylernorlund.com"
        }/${firstImage.cdn_s3_key}`
      : "";

  const svgWidth = firstImage ? firstImage.width : defaultSvgWidth;
  const svgHeight = firstImage ? firstImage.height : defaultSvgHeight;

  const maxDisplayWidth = 400;
  const scaleFactor = Math.min(1, maxDisplayWidth / svgWidth);
  const displayWidth = svgWidth * scaleFactor;
  const displayHeight = svgHeight * scaleFactor;

  useEffect(() => {
    if (!inView) {
      setResetKey((k) => k + 1);
    }
  }, [inView]);

  if (error) {
    return (
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          minHeight: displayHeight,
          alignItems: "center",
        }}
      >
        Error loading image details
      </div>
    );
  }

  return (
    <div ref={ref}>
      <DBSCANControls
        epsilon={epsilon}
        minPoints={minPoints}
        onEpsilonChange={setEpsilon}
        onMinPointsChange={setMinPoints}
      />
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          minHeight: displayHeight,
          alignItems: "center",
        }}
      >
        <div
          style={{
            height: displayHeight,
            width: displayWidth,
            borderRadius: "15px",
            overflow: "hidden",
          }}
        >
          {imageDetails && formatSupport ? (
            <svg
              key={resetKey}
              onClick={() => setResetKey((k) => k + 1)}
              viewBox={`0 0 ${svgWidth} ${svgHeight}`}
              width={displayWidth}
              height={displayHeight}
            >
              <image
                href={cdnUrl}
                x="0"
                y="0"
                width={svgWidth}
                height={svgHeight}
              />

              {lineTransitions((style, line) => {
                const x1 = line.top_left.x * svgWidth;
                const y1 = (1 - line.top_left.y) * svgHeight;
                const x2 = line.top_right.x * svgWidth;
                const y2 = (1 - line.top_right.y) * svgHeight;
                const x3 = line.bottom_right.x * svgWidth;
                const y3 = (1 - line.bottom_right.y) * svgHeight;
                const x4 = line.bottom_left.x * svgWidth;
                const y4 = (1 - line.bottom_left.y) * svgHeight;
                const points = `${x1},${y1} ${x2},${y2} ${x3},${y3} ${x4},${y4}`;
                return (
                  <animated.polygon
                    key={`${line.line_id}`}
                    style={style}
                    points={points}
                    fill="none"
                    stroke="gray"
                    strokeWidth="1"
                  />
                );
              })}

              {clusterTransitions((style, cluster) => {
                const clusterIndex = clustersWithKeys.findIndex(c => c.key === cluster.key);
                const color = colors[clusterIndex % colors.length];
                return (
                  <animated.g key={cluster.key} style={style}>
                    {cluster.lines.map((line) => {
                      const x1 = line.top_left.x * svgWidth;
                      const y1 = (1 - line.top_left.y) * svgHeight;
                      const x2 = line.top_right.x * svgWidth;
                      const y2 = (1 - line.top_right.y) * svgHeight;
                      const x3 = line.bottom_right.x * svgWidth;
                      const y3 = (1 - line.bottom_right.y) * svgHeight;
                      const x4 = line.bottom_left.x * svgWidth;
                      const y4 = (1 - line.bottom_left.y) * svgHeight;
                      const points = `${x1},${y1} ${x2},${y2} ${x3},${y3} ${x4},${y4}`;
                      return (
                        <polygon
                          key={`cluster-${clusterIndex}-line-${line.line_id}`}
                          points={points}
                          fill={color}
                          fillOpacity="0.2"
                          stroke={color}
                          strokeWidth="2"
                        />
                      );
                    })}
                    
                    <rect
                      x={cluster.boundingBox.topLeft.x * svgWidth}
                      y={(1 - cluster.boundingBox.topLeft.y) * svgHeight}
                      width={(cluster.boundingBox.topRight.x - cluster.boundingBox.topLeft.x) * svgWidth}
                      height={(cluster.boundingBox.topLeft.y - cluster.boundingBox.bottomLeft.y) * svgHeight}
                      fill="none"
                      stroke={color}
                      strokeWidth="3"
                      strokeDasharray="5,5"
                    />

                    <circle
                      cx={cluster.centroid.x * svgWidth}
                      cy={(1 - cluster.centroid.y) * svgHeight}
                      r="5"
                      fill={color}
                    />
                  </animated.g>
                );
              })}

              {noiseLines.map((line) => {
                const cx = (line.top_left.x + line.top_right.x + line.bottom_left.x + line.bottom_right.x) / 4 * svgWidth;
                const cy = (1 - (line.top_left.y + line.top_right.y + line.bottom_left.y + line.bottom_right.y) / 4) * svgHeight;
                return (
                  <circle
                    key={`noise-${line.line_id}`}
                    cx={cx}
                    cy={cy}
                    r="3"
                    fill="red"
                    opacity="0.7"
                  />
                );
              })}

              <text x="10" y="30" fill="white" fontSize="16" fontWeight="bold">
                Clusters: {clusters.length}, Noise: {noiseLines.length}
              </text>
            </svg>
          ) : (
            <div
              style={{
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                width: "100%",
                height: "100%",
              }}
            >
              Loading...
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default PhotoReceiptDBSCAN;