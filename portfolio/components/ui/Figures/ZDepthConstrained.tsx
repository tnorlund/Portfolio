import { useSpring, animated, to } from "@react-spring/web";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import React, { useEffect } from "react";

const ZDepthConstrained: React.FC = () => {
  // Set up Intersection Observer
  const [ref, inView] = useOptimizedInView({ threshold: 0.3 });

  // Set up react-spring for opacity
  const [fadeStyles, fadeApi] = useSpring(() => ({
    opacity: 0, // Start fully transparent
    config: { tension: 120, friction: 14 },
  }));

  const [floatStyles, floatApi] = useSpring(() => ({
    y: 0,
    config: { tension: 60, friction: 20 },
  }));

  const [horizStyles, horizApi] = useSpring(() => ({
    x: 0,
    config: { tension: 40, friction: 14 },
  }));
  // Trigger the fade-in once the TypeScript component is in view
  useEffect(() => {
    if (inView) {
      fadeApi.start({ opacity: 1 }); // Animate from 0 to 1
    }
  }, [inView, fadeApi]);

  useEffect(() => {
    if (inView) {
      floatApi.start({
        to: [{ y: -20 }, { y: 0 }],
        loop: { reverse: true },
      });
      horizApi.start({
        to: [{ x: -15 }, { x: 15 }],
        loop: { reverse: true },
      });
    } else {
      floatApi.stop();
      floatApi.set({ y: 0 });
      horizApi.stop();
      horizApi.set({ x: 0 });
    }
  }, [inView, floatApi, horizApi]);
  return (
    <div style={{ display: "flex", justifyContent: "center" }}>
      <div ref={ref}>
        <animated.div style={fadeStyles}>
          <div>
            <svg>
              <g>
                <polygon
                  points="96.09 127.26 137.48 81.33 203.91 81.33 162.52 127.26 96.09 127.26"
                  fill="var(--background-color)"
                />
                <path
                  d="M202.79,81.83l-40.49,44.92h-65.09l40.49-44.92h65.09M205.03,80.83h-67.78l-42.29,46.92h67.78l42.29-46.92h0Z"
                  fill="var(--text-color)"
                />
              </g>
              <animated.g
                style={{
                  transform: horizStyles.x.to((x) => `translateX(${x}px)`),
                }}
              >
                <animated.g
                  style={{
                    transform: floatStyles.y.to((y) => `translateY(${y}px)`),
                  }}
                >
                  <polygon
                    points="96.09 68.67 137.48 22.74 203.91 22.74 162.52 68.67 96.09 68.67"
                    fill="var(--code-background)"
                  />
                  <path
                    d="M202.79,23.24l-40.49,44.92h-65.09l40.49-44.92h65.09M205.03,22.24h-67.78l-42.29,46.92h67.78l42.29-46.92h0Z"
                    fill="var(--text-color)"
                  />
                </animated.g>
              </animated.g>
              <g>
                <animated.line
                  x1={horizStyles.x.to((x) => 94.97 + x)}
                  x2="94.97"
                  y2="124.17"
                  y1={floatStyles.y.to((y) => 72.84 + y)}
                  fill="none"
                  stroke="var(--text-color)"
                  strokeMiterlimit={10}
                />
                <animated.g
                  style={{
                    transform: horizStyles.x.to((x) => `translateX(${x}px)`),
                  }}
                >
                  <animated.g
                    style={{
                      transform: floatStyles.y.to((y) => `translateY(${y}px)`),
                    }}
                  >
                    <polygon
                      points="92.47 73.57 94.97 69.25 97.46 73.57"
                      fill="var(--text-color)"
                    />
                  </animated.g>
                </animated.g>
                <polygon
                  points="92.47 123.44 94.97 127.76 97.46 123.44 92.47 123.44"
                  fill="var(--text-color)"
                />
              </g>
            </svg>
          </div>
        </animated.div>
      </div>
    </div>
  );
};

export default ZDepthConstrained;
