import { animated, useSpring } from "@react-spring/web";
import type { CropViewBox } from "../Figures/utils/smartCrop";

export interface Line {
  top_left: { x: number; y: number };
  top_right: { x: number; y: number };
  bottom_left: { x: number; y: number };
  bottom_right: { x: number; y: number };
}

export interface AnimatedLineBoxProps {
  line: Line;
  svgWidth: number;
  svgHeight: number;
  delay: number;
  cropInfo?: CropViewBox | null;
  fullImageWidth?: number;
  fullImageHeight?: number;
}

const AnimatedLineBox = ({
  line,
  svgWidth,
  svgHeight,
  delay,
  cropInfo,
  fullImageWidth,
  fullImageHeight,
}: AnimatedLineBoxProps) => {
  // Transform normalized coordinates to SVG pixel coordinates
  // The viewBox handles the crop offset, so we just convert to full image coordinates
  const imgWidth = fullImageWidth ?? svgWidth;
  const imgHeight = fullImageHeight ?? svgHeight;
  const transformX = (normX: number) => normX * imgWidth;
  const transformY = (normY: number) => (1 - normY) * imgHeight;

  // Convert normalized coordinates to absolute pixel values.
  const x1 = transformX(line.top_left.x);
  const y1 = transformY(line.top_left.y);
  const x2 = transformX(line.top_right.x);
  const y2 = transformY(line.top_right.y);
  const x3 = transformX(line.bottom_right.x);
  const y3 = transformY(line.bottom_right.y);
  const x4 = transformX(line.bottom_left.x);
  const y4 = transformY(line.bottom_left.y);
  const points = `${x1},${y1} ${x2},${y2} ${x3},${y3} ${x4},${y4}`;

  // Compute the polygon's centroid.
  const centroidX = (x1 + x2 + x3 + x4) / 4;
  const centroidY = (y1 + y2 + y3 + y4) / 4;

  // Animate the polygon scaling from 0 to 1, with the centroid as the origin.
  const polygonSpring = useSpring({
    from: { transform: "scale(0)" },
    to: { transform: "scale(1)" },
    delay: delay,
    config: { duration: 800 },
  });

  // Animate the centroid marker - midY is the center of the visible area
  const midY = cropInfo
    ? cropInfo.y + cropInfo.height / 2  // Center of crop region in full image coords
    : imgHeight / 2;
  const centroidSpring = useSpring({
    from: { opacity: 0, cy: centroidY },
    to: async next => {
      await next({ opacity: 1, cy: centroidY, config: { duration: 300 } });
      await next({ cy: midY, config: { duration: 800 } });
    },
    delay: delay + 30,
  });

  return (
    <>
      <animated.polygon
        style={{
          ...polygonSpring,
          transformOrigin: "50% 50%",
          transformBox: "fill-box",
        }}
        points={points}
        fill="none"
        stroke="var(--color-red)"
        strokeWidth="2"
      />
      <animated.circle
        cx={centroidX}
        cy={centroidSpring.cy}
        r={10}
        fill="var(--color-red)"
        style={{ opacity: centroidSpring.opacity }}
      />
    </>
  );
};

export default AnimatedLineBox;
