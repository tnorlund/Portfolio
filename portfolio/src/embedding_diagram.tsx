import React, { useState, useEffect } from "react";
import { useSpring, animated } from "@react-spring/web";
import { useInView } from "react-intersection-observer";

const EmbeddingDiagram = () => {
  const [ref, inView] = useInView({ threshold: 0.3, triggerOnce: true });

  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const updateSize = () => setIsMobile(window.innerWidth < 768);
    updateSize();
    window.addEventListener("resize", updateSize);
    return () => window.removeEventListener("resize", updateSize);
  }, []);

  // SPRINGS for animated opacity of each step
  const [ocrStyles, ocrApi] = useSpring(() => ({ opacity: 0 }));
  const [embedStyles, embedApi] = useSpring(() => ({ opacity: 0 }));
  const [similarStyles, similarApi] = useSpring(() => ({ opacity: 0 }));
  const [labelStyles, labelApi] = useSpring(() => ({ opacity: 0 }));

  useEffect(() => {
    if (inView) {
      ocrApi.start({ opacity: 1, config: { tension: 500, friction: 20 } });
      embedApi.start({
        opacity: 1,
        delay: 500,
        config: { tension: 80, friction: 20 },
      });
      similarApi.start({
        opacity: 1,
        delay: 1000,
        config: { tension: 80, friction: 20 },
      });
      labelApi.start({
        opacity: 1,
        delay: 1500,
        config: { tension: 80, friction: 20 },
      });
    }
  }, [inView, ocrApi, embedApi, similarApi, labelApi]);

  const HorizontalEmbeddingDiagram = () => (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="600"
      height="150"
      viewBox="0 0 600 140"
    >
      <g fontFamily="sans-serif" fontSize="14" fill="var(--text-color)">
        <animated.g style={ocrStyles}>
          <rect
            x="20"
            y="40"
            width="120"
            height="60"
            rx="8"
            fill="var(--background-color)"
            stroke="var(--text-color)"
            strokeWidth="2"
          />
          <text x="80" y="75" textAnchor="middle">
            OCR
          </text>
        </animated.g>
        <animated.g style={embedStyles}>
          <rect
            x="160"
            y="40"
            width="120"
            height="60"
            rx="8"
            fill="var(--background-color)"
            stroke="var(--text-color)"
            strokeWidth="2"
          />
          <text x="220" y="75" textAnchor="middle">
            Embedding
          </text>
        </animated.g>
        <animated.g style={similarStyles}>
          <rect
            x="300"
            y="40"
            width="140"
            height="60"
            rx="8"
            fill="var(--background-color)"
            stroke="var(--text-color)"
            strokeWidth="2"
          />
          <text x="370" y="67" textAnchor="middle">
            <tspan x="370" dy="0">
              Similar
            </tspan>
            <tspan x="370" dy="1.2em">
              Words
            </tspan>
          </text>
        </animated.g>
        <animated.g style={labelStyles}>
          <rect
            x="460"
            y="40"
            width="120"
            height="60"
            rx="8"
            fill="var(--background-color)"
            stroke="var(--text-color)"
            strokeWidth="2"
          />
          <text x="520" y="67" textAnchor="middle">
            <tspan x="520" dy="0">
              Guessed
            </tspan>
            <tspan x="520" dy="1.2em">
              Label
            </tspan>
          </text>
        </animated.g>
        {/* Marker defs remain outside animated groups */}
        <defs>
          <marker
            id="arrow"
            markerWidth="10"
            markerHeight="10"
            refX="6"
            refY="3"
            orient="auto"
            markerUnits="strokeWidth"
          >
            <path d="M0,0 L0,6 L9,3 z" fill="black" />
          </marker>
        </defs>
      </g>
    </svg>
  );

  const VerticalEmbeddingDiagram = () => (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="200"
      height="400"
      viewBox="0 0 200 400"
    >
      <g fontFamily="sans-serif" fontSize="14" fill="var(--text-color)">
        <animated.g style={ocrStyles}>
          <rect
            x="40"
            y="20"
            width="120"
            height="60"
            rx="8"
            fill="var(--background-color)"
            stroke="var(--text-color)"
            strokeWidth="2"
          />
          <text x="100" y="55" textAnchor="middle">
            OCR
          </text>
        </animated.g>
        <animated.g style={embedStyles}>
          <rect
            x="40"
            y="120"
            width="120"
            height="60"
            rx="8"
            fill="var(--background-color)"
            stroke="var(--text-color)"
            strokeWidth="2"
          />
          <text x="100" y="155" textAnchor="middle">
            Embedding
          </text>
        </animated.g>
        <animated.g style={similarStyles}>
          <rect
            x="30"
            y="220"
            width="140"
            height="60"
            rx="8"
            fill="var(--background-color)"
            stroke="var(--text-color)"
            strokeWidth="2"
          />
          <text x="100" y="247" textAnchor="middle">
            <tspan x="100" dy="0">
              Similar
            </tspan>
            <tspan x="100" dy="1.2em">
              Words
            </tspan>
          </text>
        </animated.g>
        <animated.g style={labelStyles}>
          <rect
            x="40"
            y="320"
            width="120"
            height="60"
            rx="8"
            fill="var(--background-color)"
            stroke="var(--text-color)"
            strokeWidth="2"
          />
          <text x="100" y="347" textAnchor="middle">
            <tspan x="100" dy="0">
              Guessed
            </tspan>
            <tspan x="100" dy="1.2em">
              Label
            </tspan>
          </text>
        </animated.g>
        {/* Marker defs remain outside animated groups */}
        <defs>
          <marker
            id="arrow"
            markerWidth="10"
            markerHeight="10"
            refX="6"
            refY="3"
            orient="auto"
            markerUnits="strokeWidth"
          >
            <path d="M0,0 L0,6 L9,3 z" fill="black" />
          </marker>
        </defs>
      </g>
    </svg>
  );

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      <div className="embedding-diagram" ref={ref}>
        {isMobile ? (
          <VerticalEmbeddingDiagram />
        ) : (
          <HorizontalEmbeddingDiagram />
        )}
      </div>
    </div>
  );
};

export default EmbeddingDiagram;
