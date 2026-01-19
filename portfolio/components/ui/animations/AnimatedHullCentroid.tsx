import React from "react";
import { animated, useSpring } from "@react-spring/web";
import type { Point } from "../../../types/api";
import type { CropViewBox } from "../Figures/utils/smartCrop";

interface AnimatedHullCentroidProps {
  centroid: Point;
  svgWidth: number;
  svgHeight: number;
  delay: number;
  cropInfo?: CropViewBox | null;
  fullImageWidth?: number;
  fullImageHeight?: number;
}

const AnimatedHullCentroid: React.FC<AnimatedHullCentroidProps> = ({
  centroid,
  svgWidth,
  svgHeight,
  delay,
  cropInfo,
  fullImageWidth,
  fullImageHeight,
}) => {
  const centroidSpring = useSpring({
    from: { opacity: 0, scale: 0 },
    to: { opacity: 1, scale: 1 },
    delay: delay,
    config: { duration: 600 },
  });

  // Transform normalized coordinates to SVG coordinates
  const transformX = (normX: number) => {
    if (cropInfo && fullImageWidth) {
      return normX * fullImageWidth - cropInfo.x;
    }
    return normX * svgWidth;
  };
  
  const transformY = (normY: number) => {
    if (cropInfo && fullImageHeight) {
      return (1 - normY) * fullImageHeight - cropInfo.y;
    }
    return (1 - normY) * svgHeight;
  };

  const centroidX = transformX(centroid.x);
  const centroidY = transformY(centroid.y);

  return (
    <animated.circle
      cx={centroidX}
      cy={centroidY}
      r={15}
      fill="var(--color-red)"
      strokeWidth="3"
      style={{
        opacity: centroidSpring.opacity,
        transform: centroidSpring.scale.to(s => `scale(${s})`),
        transformOrigin: `${centroidX}px ${centroidY}px`,
      }}
    />
  );
};

export default AnimatedHullCentroid;
