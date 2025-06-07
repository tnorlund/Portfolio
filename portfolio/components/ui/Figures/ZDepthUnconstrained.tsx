import { useSpring, animated, to } from "@react-spring/web";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import React, { useEffect } from "react";

const ZDepthUnconstrained: React.FC = () => {
  const [ref, inView] = useOptimizedInView({ threshold: 0.3 });

  // Set up react-spring for opacity
  const [fadeStyles, fadeApi] = useSpring(() => ({
    opacity: 0, // Start fully transparent
    config: { tension: 120, friction: 14 },
  }));

  // Add float and horizontal springs
  const [floatStyles, floatApi] = useSpring(() => ({
    y: 0,
    config: { tension: 60, friction: 20 },
  }));

  const [horizStyles, horizApi] = useSpring(() => ({
    x: 0,
    config: { tension: 40, friction: 14 },
  }));

  const [shearStyles, shearApi] = useSpring(() => ({
    s: -10, // degrees
    config: { tension: 35, friction: 12 },
  }));

  // Trigger the fade-in and spring animations once the component is in view
  React.useEffect(() => {
    if (inView) {
      fadeApi.start({ opacity: 1 }); // Fade-in
      floatApi.start({
        to: [{ y: -5 }, { y: 0 }],
        loop: { reverse: true },
      });
      horizApi.start({
        to: [{ x: -5 }, { x: 5 }],
        loop: { reverse: true },
      });
      shearApi.start({
        to: [{ s: -5 }, { s: 10 }],
        loop: { reverse: true },
      });
    } else {
      floatApi.stop();
      floatApi.set({ y: 0 });
      horizApi.stop();
      horizApi.set({ x: 0 });
      shearApi.stop();
      shearApi.set({ s: -5 });
    }
  }, [inView, fadeApi, floatApi, horizApi, shearApi]);

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
                    transform: to(
                      [floatStyles.y, shearStyles.s],
                      (y, s) => `translateY(${y}px) skewY(${s}deg)`
                    ),
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
            </svg>
          </div>
        </animated.div>
      </div>
    </div>
  );
};

export default ZDepthUnconstrained;
