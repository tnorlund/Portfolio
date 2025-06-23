import { useSpring, animated } from "@react-spring/web";

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
}

const AnimatedLineBox = ({
  line,
  svgWidth,
  svgHeight,
  delay,
}: AnimatedLineBoxProps) => {
  // Convert normalized coordinates to absolute pixel values.
  const x1 = line.top_left.x * svgWidth;
  const y1 = (1 - line.top_left.y) * svgHeight;
  const x2 = line.top_right.x * svgWidth;
  const y2 = (1 - line.top_right.y) * svgHeight;
  const x3 = line.bottom_right.x * svgWidth;
  const y3 = (1 - line.bottom_right.y) * svgHeight;
  const x4 = line.bottom_left.x * svgWidth;
  const y4 = (1 - line.bottom_left.y) * svgHeight;
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

  // Animate the centroid marker.
  const centroidSpring = useSpring({
    from: { opacity: 0, cy: centroidY },
    to: async next => {
      await next({ opacity: 1, cy: centroidY, config: { duration: 300 } });
      await next({ cy: svgHeight / 2, config: { duration: 800 } });
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
