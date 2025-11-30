import { animated, useSpring } from "@react-spring/web";
import React, { useEffect, useRef } from "react";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import styles from "./GitBranchDiagram.module.css";

interface GitBranchDiagramProps {
  /** Optional prop for future enhancements */
  enableAnimation?: boolean;
}

const GitBranchDiagram: React.FC<GitBranchDiagramProps> = ({ enableAnimation = true }) => {
  // Start animation when component comes into view
  const [ref, inView] = useOptimizedInView({ threshold: 0.3 });

  // Track animation cycle for replay on click
  const [animationCycle, setAnimationCycle] = React.useState(0);

  // Use inView to control whether animations should run
  const shouldAnimate = enableAnimation && (inView || animationCycle > 0);

  // Handle click to replay animation
  const handleClick = () => {
    setAnimationCycle((prev) => prev + 1);
  };
  // Animation timing constants
  const CIRCLE_DURATION = 300;
  const LINE_DURATION = 400;
  const STAGGER = 150; // delay between each animation step

  // Animation delays for each step (in order)
  // Order: blue1 circle -> red1 circle -> red1 line -> red2 circle -> red2 line ->
  //        blue2 circle -> blue1 line -> blue3 circle -> blue2 line ->
  //        blue4 circle -> blue4 lines -> green circle -> green line
  // Each circle waits for previous line(s) to complete
  const delays = {
    blue1: 0,
    red1: STAGGER,
    red1Line: STAGGER + CIRCLE_DURATION, // Start after red1 circle completes
    red2: STAGGER + CIRCLE_DURATION + LINE_DURATION, // Start after red1 line completes
    red2Line: STAGGER + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION, // Start after red2 circle completes
    blue2: STAGGER + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION + LINE_DURATION, // Start after red2 line completes
    blue1Line: STAGGER + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION, // Start after blue2 circle completes
    blue3: STAGGER + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION + LINE_DURATION, // Start after blue1 line completes
    blue2Line: STAGGER + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION, // Start after blue3 circle completes
    blue4: STAGGER + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION + LINE_DURATION, // Start after blue2 line completes
    blue4Lines: STAGGER + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION, // Start after blue4 circle completes
    green: STAGGER + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION + LINE_DURATION, // Start after blue4 lines complete
    greenLine: STAGGER + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION + LINE_DURATION + CIRCLE_DURATION, // Start after green circle completes
  };

  // Helper to get path length for stroke animation
  const getPathLength = (pathElement: SVGPathElement | SVGLineElement | null): number => {
    if (!pathElement) return 0;
    if (pathElement instanceof SVGLineElement) {
      const x1 = pathElement.x1.baseVal.value;
      const y1 = pathElement.y1.baseVal.value;
      const x2 = pathElement.x2.baseVal.value;
      const y2 = pathElement.y2.baseVal.value;
      return Math.sqrt(Math.pow(x2 - x1, 2) + Math.pow(y2 - y1, 2));
    }
    return pathElement.getTotalLength();
  };

  // Refs for path elements to calculate lengths
  const vertLineRefs = {
    blue1Line: useRef<SVGLineElement>(null),
    red1Line: useRef<SVGPathElement>(null),
    red2Line: useRef<SVGLineElement>(null),
    blue2Line: useRef<SVGLineElement>(null),
    blue3Line: useRef<SVGLineElement>(null),
    blue4Line1: useRef<SVGLineElement>(null),
    blue4Line2: useRef<SVGPathElement>(null),
    greenLine: useRef<SVGPathElement>(null),
  };

  const horizLineRefs = {
    blue1Line: useRef<SVGLineElement>(null),
    red1Line: useRef<SVGPathElement>(null),
    red2Line: useRef<SVGLineElement>(null),
    blue2Line: useRef<SVGLineElement>(null),
    blue3Line: useRef<SVGLineElement>(null),
    blue4Line1: useRef<SVGLineElement>(null),
    blue4Line2: useRef<SVGPathElement>(null),
    greenLine: useRef<SVGPathElement>(null),
  };

  // Circle animations - scale from 0 to 1 (using imperative API for replay)
  const [blue1Circle, blue1CircleApi] = useSpring(() => ({
    scale: 0,
    config: { duration: CIRCLE_DURATION },
  }));

  const [red1Circle, red1CircleApi] = useSpring(() => ({
    scale: 0,
    config: { duration: CIRCLE_DURATION },
  }));

  const [red2Circle, red2CircleApi] = useSpring(() => ({
    scale: 0,
    config: { duration: CIRCLE_DURATION },
  }));

  const [blue2Circle, blue2CircleApi] = useSpring(() => ({
    scale: 0,
    config: { duration: CIRCLE_DURATION },
  }));

  const [blue3Circle, blue3CircleApi] = useSpring(() => ({
    scale: 0,
    config: { duration: CIRCLE_DURATION },
  }));

  const [blue4Circle, blue4CircleApi] = useSpring(() => ({
    scale: 0,
    config: { duration: CIRCLE_DURATION },
  }));

  const [greenCircle, greenCircleApi] = useSpring(() => ({
    scale: 0,
    config: { duration: CIRCLE_DURATION },
  }));

  // State to store path lengths for stroke animation
  const [pathLengths, setPathLengths] = React.useState<{
    vert: Record<string, number>;
    horiz: Record<string, number>;
  }>({ vert: {}, horiz: {} });

  // Calculate path lengths after mount
  useEffect(() => {
    const vertLengths: Record<string, number> = {};
    const horizLengths: Record<string, number> = {};

    Object.entries(vertLineRefs).forEach(([key, ref]) => {
      if (ref.current) {
        vertLengths[key] = getPathLength(ref.current);
      }
    });

    Object.entries(horizLineRefs).forEach(([key, ref]) => {
      if (ref.current) {
        horizLengths[key] = getPathLength(ref.current);
      }
    });

    setPathLengths({ vert: vertLengths, horiz: horizLengths });
  }, []);

  // Line animations - draw from start to end
  // Use a default length that will be updated once path lengths are calculated
  const DEFAULT_STROKE_LENGTH = 1000;

  // Get path lengths with fallback
  const getPathLengthForSpring = (key: string, isVert: boolean) => {
    return isVert
      ? (pathLengths.vert[key] || DEFAULT_STROKE_LENGTH)
      : (pathLengths.horiz[key] || DEFAULT_STROKE_LENGTH);
  };

  // Vertical layout path lengths
  const vertBlue1LineLength = getPathLengthForSpring('blue1Line', true);
  const vertRed1LineLength = getPathLengthForSpring('red1Line', true);
  const vertRed2LineLength = getPathLengthForSpring('red2Line', true);
  const vertBlue2LineLength = getPathLengthForSpring('blue2Line', true);
  const vertBlue3LineLength = getPathLengthForSpring('blue3Line', true);
  const vertBlue4Line1Length = getPathLengthForSpring('blue4Line1', true);
  const vertBlue4Line2Length = getPathLengthForSpring('blue4Line2', true);
  const vertGreenLineLength = getPathLengthForSpring('greenLine', true);

  // Horizontal layout path lengths
  const horizBlue1LineLength = getPathLengthForSpring('blue1Line', false);
  const horizRed1LineLength = getPathLengthForSpring('red1Line', false);
  const horizRed2LineLength = getPathLengthForSpring('red2Line', false);
  const horizBlue2LineLength = getPathLengthForSpring('blue2Line', false);
  const horizBlue3LineLength = getPathLengthForSpring('blue3Line', false);
  const horizBlue4Line1Length = getPathLengthForSpring('blue4Line1', false);
  const horizBlue4Line2Length = getPathLengthForSpring('blue4Line2', false);
  const horizGreenLineLength = getPathLengthForSpring('greenLine', false);

  // Use max of vertical and horizontal lengths for springs (works for both layouts)
  const blue1LineMaxLength = Math.max(vertBlue1LineLength, horizBlue1LineLength);
  const red1LineMaxLength = Math.max(vertRed1LineLength, horizRed1LineLength);
  const red2LineMaxLength = Math.max(vertRed2LineLength, horizRed2LineLength);
  const blue2LineMaxLength = Math.max(vertBlue2LineLength, horizBlue2LineLength);
  const blue3LineMaxLength = Math.max(vertBlue3LineLength, horizBlue3LineLength);
  const blue4Line1MaxLength = Math.max(vertBlue4Line1Length, horizBlue4Line1Length);
  const blue4Line2MaxLength = Math.max(vertBlue4Line2Length, horizBlue4Line2Length);
  const greenLineMaxLength = Math.max(vertGreenLineLength, horizGreenLineLength);

  // Line animations using imperative API for replay
  const [blue1Line, blue1LineApi] = useSpring(() => ({
    strokeDashoffset: blue1LineMaxLength,
    config: { duration: LINE_DURATION },
  }));

  const [red1Line, red1LineApi] = useSpring(() => ({
    strokeDashoffset: -red1LineMaxLength, // Start at negative for reverse animation
    config: { duration: LINE_DURATION },
  }));

  const [red2Line, red2LineApi] = useSpring(() => ({
    strokeDashoffset: red2LineMaxLength,
    config: { duration: LINE_DURATION },
  }));

  const [blue2Line, blue2LineApi] = useSpring(() => ({
    strokeDashoffset: blue2LineMaxLength,
    config: { duration: LINE_DURATION },
  }));

  const [blue3Line, blue3LineApi] = useSpring(() => ({
    strokeDashoffset: blue3LineMaxLength,
    config: { duration: LINE_DURATION },
  }));

  const [blue4Line1, blue4Line1Api] = useSpring(() => ({
    strokeDashoffset: blue4Line1MaxLength,
    config: { duration: LINE_DURATION },
  }));

  const [blue4Line2, blue4Line2Api] = useSpring(() => ({
    strokeDashoffset: blue4Line2MaxLength,
    config: { duration: LINE_DURATION },
  }));

  const [greenLine, greenLineApi] = useSpring(() => ({
    strokeDashoffset: -greenLineMaxLength, // Start at negative for reverse animation
    config: { duration: LINE_DURATION },
  }));

  // Trigger animations when shouldAnimate changes or on click
  useEffect(() => {
    if (shouldAnimate) {
      // Reset all to initial state immediately
      blue1CircleApi.set({ scale: 0 });
      red1CircleApi.set({ scale: 0 });
      red2CircleApi.set({ scale: 0 });
      blue2CircleApi.set({ scale: 0 });
      blue3CircleApi.set({ scale: 0 });
      blue4CircleApi.set({ scale: 0 });
      greenCircleApi.set({ scale: 0 });

      blue1LineApi.set({ strokeDashoffset: blue1LineMaxLength });
      red1LineApi.set({ strokeDashoffset: -red1LineMaxLength }); // Start at negative for reverse animation
      red2LineApi.set({ strokeDashoffset: red2LineMaxLength });
      blue2LineApi.set({ strokeDashoffset: blue2LineMaxLength });
      blue3LineApi.set({ strokeDashoffset: blue3LineMaxLength });
      blue4Line1Api.set({ strokeDashoffset: blue4Line1MaxLength });
      blue4Line2Api.set({ strokeDashoffset: blue4Line2MaxLength });
      greenLineApi.set({ strokeDashoffset: -greenLineMaxLength }); // Start at negative for reverse animation

      // Small delay to ensure reset completes, then animate
      const timeout = setTimeout(() => {
        // Animate circles
        blue1CircleApi.start({ scale: 1, delay: delays.blue1 });
        red1CircleApi.start({ scale: 1, delay: delays.red1 });
        red2CircleApi.start({ scale: 1, delay: delays.red2 });
        blue2CircleApi.start({ scale: 1, delay: delays.blue2 });
        blue3CircleApi.start({ scale: 1, delay: delays.blue3 });
        blue4CircleApi.start({ scale: 1, delay: delays.blue4 });
        greenCircleApi.start({ scale: 1, delay: delays.green });

        // Animate lines (after their starting circles complete)
        red1LineApi.start({ strokeDashoffset: 0, delay: delays.red1Line }); // Reverse: animate from negative to 0 (draws from end/blue to start/red)
        red2LineApi.start({ strokeDashoffset: 0, delay: delays.red2Line });
        blue1LineApi.start({ strokeDashoffset: 0, delay: delays.blue1Line }); // connects blue1 to blue2, after blue2 circle
        blue2LineApi.start({ strokeDashoffset: 0, delay: delays.blue2Line }); // connects blue2 to blue3, after blue3 circle
        blue4Line1Api.start({ strokeDashoffset: 0, delay: delays.blue4Lines });
        blue4Line2Api.start({ strokeDashoffset: 0, delay: delays.blue4Lines });
        greenLineApi.start({ strokeDashoffset: 0, delay: delays.greenLine }); // Reverse: animate from negative to 0 (draws from end/blue to start/green)
      }, 10);

      return () => clearTimeout(timeout);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shouldAnimate, animationCycle]);
  return (
    <div
      ref={ref}
      onClick={handleClick}
      style={{
        display: "flex",
        justifyContent: "center",
        marginTop: "1em",
        marginBottom: "1em",
        cursor: "pointer",
      }}
    >
      <div>
        {/* Vertical layout (mobile) */}
        <div className={styles["mobile-only"]}>
          <div>
            <svg height="400" width="300" viewBox="0 0 300 400">
              <defs>
                <style>
                  {`
                    .git-branch-line {
                      fill: none;
                      stroke: var(--text-color);
                      stroke-miterlimit: 10;
                      stroke-width: 7px;
                    }
                  `}
                </style>
              </defs>

              {/* Git branch lines */}
              {/* Blue1 line: from first blue to second blue */}
              <animated.line
                ref={vertLineRefs.blue1Line}
                className="git-branch-line"
                x1="150"
                y1="25"
                x2="150"
                y2="112.5"
                strokeDasharray={vertBlue1LineLength}
                style={{
                  strokeDashoffset: blue1Line.strokeDashoffset,
                }}
              />
              {/* Red1 line: from first blue to first red */}
              <animated.path
                ref={vertLineRefs.red1Line}
                className="git-branch-line"
                d="M237.81,112.5c0-87.5-87.81-87.5-87.81-87.5"
                strokeDasharray={vertRed1LineLength}
                style={{
                  strokeDashoffset: red1Line.strokeDashoffset,
                }}
              />
              {/* Red2 line: from first red to second red */}
              <animated.line
                ref={vertLineRefs.red2Line}
                className="git-branch-line"
                x1="237.81"
                y1="112.5"
                x2="237.81"
                y2="200"
                strokeDasharray={vertRed2LineLength}
                style={{
                  strokeDashoffset: red2Line.strokeDashoffset,
                }}
              />
              {/* Blue2 line: from first blue to second blue (vertical) */}
              <animated.line
                ref={vertLineRefs.blue2Line}
                className="git-branch-line"
                x1="150"
                y1="112.5"
                x2="150"
                y2="200"
                strokeDasharray={vertBlue2LineLength}
                style={{
                  strokeDashoffset: blue2Line.strokeDashoffset,
                }}
              />
              {/* Blue3 line: from second blue to third blue */}
              <animated.line
                ref={vertLineRefs.blue3Line}
                className="git-branch-line"
                x1="150"
                y1="199.73"
                x2="150"
                y2="287.23"
                strokeDasharray={vertBlue3LineLength}
                style={{
                  strokeDashoffset: blue3Line.strokeDashoffset,
                }}
              />
              {/* Blue4 line 1: from third blue to fourth blue */}
              <animated.line
                ref={vertLineRefs.blue4Line1}
                className="git-branch-line"
                x1="150"
                y1="199.73"
                x2="150"
                y2="287.23"
                strokeDasharray={vertBlue4Line1Length}
                style={{
                  strokeDashoffset: blue4Line1.strokeDashoffset,
                }}
              />
              {/* Blue4 line 2: from second red to fourth blue */}
              <animated.path
                ref={vertLineRefs.blue4Line2}
                className="git-branch-line"
                d="M237.81,200s0,87.5-87.81,87.5"
                strokeDasharray={vertBlue4Line2Length}
                style={{
                  strokeDashoffset: blue4Line2.strokeDashoffset,
                }}
              />
              {/* Green line: from fourth blue to green */}
              <animated.path
                ref={vertLineRefs.greenLine}
                className="git-branch-line"
                d="M62.19,375c0-87.5,87.81-87.5,87.81-87.5"
                strokeDasharray={vertGreenLineLength}
                style={{
                  strokeDashoffset: greenLine.strokeDashoffset,
                }}
              />

              {/* Git branch nodes (commits) - rendered after lines so they appear on top */}
              {/* First blue commit */}
              <animated.circle
                fill="var(--color-blue)"
                cx="150"
                cy="25"
                r="25"
                style={{
                  transform: blue1Circle.scale.to((s) => `scale(${s})`),
                  transformOrigin: "150px 25px",
                  opacity: blue1Circle.opacity,
                }}
              />
              {/* First red commit */}
              <animated.circle
                fill="var(--color-red)"
                cx="237.81"
                cy="112.5"
                r="25"
                style={{
                  transform: red1Circle.scale.to((s) => `scale(${s})`),
                  transformOrigin: "237.81px 112.5px",
                  opacity: red1Circle.opacity,
                }}
              />
              {/* Second red commit */}
              <animated.circle
                fill="var(--color-red)"
                cx="237.81"
                cy="200"
                r="25"
                style={{
                  transform: red2Circle.scale.to((s) => `scale(${s})`),
                  transformOrigin: "237.81px 200px",
                  opacity: red2Circle.opacity,
                }}
              />
              {/* Second blue commit */}
              <animated.circle
                fill="var(--color-blue)"
                cx="150"
                cy="112.5"
                r="25"
                style={{
                  transform: blue2Circle.scale.to((s) => `scale(${s})`),
                  transformOrigin: "150px 112.5px",
                  opacity: blue2Circle.opacity,
                }}
              />
              {/* Third blue commit */}
              <animated.circle
                fill="var(--color-blue)"
                cx="150"
                cy="200"
                r="25"
                style={{
                  transform: blue3Circle.scale.to((s) => `scale(${s})`),
                  transformOrigin: "150px 200px",
                  opacity: blue3Circle.opacity,
                }}
              />
              {/* Fourth blue commit */}
              <animated.circle
                fill="var(--color-blue)"
                cx="150"
                cy="287.5"
                r="25"
                style={{
                  transform: blue4Circle.scale.to((s) => `scale(${s})`),
                  transformOrigin: "150px 287.5px",
                  opacity: blue4Circle.opacity,
                }}
              />
              {/* Green commit */}
              <animated.circle
                fill="var(--color-green)"
                cx="62.19"
                cy="375"
                r="25"
                style={{
                  transform: greenCircle.scale.to((s) => `scale(${s})`),
                  transformOrigin: "62.19px 375px",
                  opacity: greenCircle.opacity,
                }}
              />
            </svg>
          </div>
        </div>

        {/* Horizontal layout (desktop) */}
        <div className={styles["desktop-only"]}>
          <div>
            <svg height="250" width="400" viewBox="0 0 400 250">
              <defs>
                <style>
                  {`
                    .git-branch-line {
                      fill: none;
                      stroke: var(--text-color);
                      stroke-miterlimit: 10;
                      stroke-width: 7px;
                    }
                  `}
                </style>
              </defs>

              {/* Git branch lines */}
              {/* Blue1 line: from first blue to second blue */}
              <animated.line
                ref={horizLineRefs.blue1Line}
                className="git-branch-line"
                x1="25"
                y1="125"
                x2="112.5"
                y2="125"
                strokeDasharray={horizBlue1LineLength}
                style={{
                  strokeDashoffset: blue1Line.strokeDashoffset,
                }}
              />
              {/* Red1 line: from first blue to first red */}
              <animated.path
                ref={horizLineRefs.red1Line}
                className="git-branch-line"
                d="M112.5,37.19c-87.5,0-87.5,87.81-87.5,87.81"
                strokeDasharray={horizRed1LineLength}
                style={{
                  strokeDashoffset: red1Line.strokeDashoffset,
                }}
              />
              {/* Red2 line: from first red to second red */}
              <animated.line
                ref={horizLineRefs.red2Line}
                className="git-branch-line"
                x1="112.5"
                y1="37.19"
                x2="200"
                y2="37.19"
                strokeDasharray={horizRed2LineLength}
                style={{
                  strokeDashoffset: red2Line.strokeDashoffset,
                }}
              />
              {/* Blue2 line: from first blue to second blue (horizontal) */}
              <animated.line
                ref={horizLineRefs.blue2Line}
                className="git-branch-line"
                x1="112.5"
                y1="125"
                x2="200"
                y2="125"
                strokeDasharray={horizBlue2LineLength}
                style={{
                  strokeDashoffset: blue2Line.strokeDashoffset,
                }}
              />
              {/* Blue3 line: from second blue to third blue */}
              <animated.line
                ref={horizLineRefs.blue3Line}
                className="git-branch-line"
                x1="199.73"
                y1="125"
                x2="287.23"
                y2="125"
                strokeDasharray={horizBlue3LineLength}
                style={{
                  strokeDashoffset: blue3Line.strokeDashoffset,
                }}
              />
              {/* Blue4 line 1: from third blue to fourth blue */}
              <animated.line
                ref={horizLineRefs.blue4Line1}
                className="git-branch-line"
                x1="199.73"
                y1="125"
                x2="287.23"
                y2="125"
                strokeDasharray={horizBlue4Line1Length}
                style={{
                  strokeDashoffset: blue4Line1.strokeDashoffset,
                }}
              />
              {/* Blue4 line 2: from second red to fourth blue */}
              <animated.path
                ref={horizLineRefs.blue4Line2}
                className="git-branch-line"
                d="M200,37.19s87.5,0,87.5,87.81"
                strokeDasharray={horizBlue4Line2Length}
                style={{
                  strokeDashoffset: blue4Line2.strokeDashoffset,
                }}
              />
              {/* Green line: from fourth blue to green */}
              <animated.path
                ref={horizLineRefs.greenLine}
                className="git-branch-line"
                d="M375,212.81c-87.77,0-87.5-87.81-87.5-87.81"
                strokeDasharray={horizGreenLineLength}
                style={{
                  strokeDashoffset: greenLine.strokeDashoffset,
                }}
              />

              {/* Git branch nodes (commits) - rendered after lines so they appear on top */}
              {/* First blue commit */}
              <animated.circle
                fill="var(--color-blue)"
                cx="25"
                cy="125"
                r="25"
                style={{
                  transform: blue1Circle.scale.to((s) => `scale(${s})`),
                  transformOrigin: "25px 125px",
                  opacity: blue1Circle.opacity,
                }}
              />
              {/* First red commit */}
              <animated.circle
                fill="var(--color-red)"
                cx="112.5"
                cy="37.19"
                r="25"
                style={{
                  transform: red1Circle.scale.to((s) => `scale(${s})`),
                  transformOrigin: "112.5px 37.19px",
                  opacity: red1Circle.opacity,
                }}
              />
              {/* Second red commit */}
              <animated.circle
                fill="var(--color-red)"
                cx="200"
                cy="37.19"
                r="25"
                style={{
                  transform: red2Circle.scale.to((s) => `scale(${s})`),
                  transformOrigin: "200px 37.19px",
                  opacity: red2Circle.opacity,
                }}
              />
              {/* Second blue commit */}
              <animated.circle
                fill="var(--color-blue)"
                cx="112.5"
                cy="125"
                r="25"
                style={{
                  transform: blue2Circle.scale.to((s) => `scale(${s})`),
                  transformOrigin: "112.5px 125px",
                  opacity: blue2Circle.opacity,
                }}
              />
              {/* Third blue commit */}
              <animated.circle
                fill="var(--color-blue)"
                cx="200"
                cy="125"
                r="25"
                style={{
                  transform: blue3Circle.scale.to((s) => `scale(${s})`),
                  transformOrigin: "200px 125px",
                  opacity: blue3Circle.opacity,
                }}
              />
              {/* Fourth blue commit */}
              <animated.circle
                fill="var(--color-blue)"
                cx="287.5"
                cy="125"
                r="25"
                style={{
                  transform: blue4Circle.scale.to((s) => `scale(${s})`),
                  transformOrigin: "287.5px 125px",
                  opacity: blue4Circle.opacity,
                }}
              />
              {/* Green commit */}
              <animated.circle
                fill="var(--color-green)"
                cx="375"
                cy="212.81"
                r="25"
                style={{
                  transform: greenCircle.scale.to((s) => `scale(${s})`),
                  transformOrigin: "375px 212.81px",
                  opacity: greenCircle.opacity,
                }}
              />
            </svg>
          </div>
        </div>

        {/* Color legend */}
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            columnGap: "24px",
            marginTop: "1rem",
            marginBottom: "1rem",
            justifyContent: "center",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <div
              style={{
                width: "16px",
                height: "16px",
                borderRadius: "50%",
                backgroundColor: "var(--color-blue)",
              }}
            />
            <span>Main</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <div
              style={{
                width: "16px",
                height: "16px",
                borderRadius: "50%",
                backgroundColor: "var(--color-red)",
              }}
            />
            <span>Bug Fix</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <div
              style={{
                width: "16px",
                height: "16px",
                borderRadius: "50%",
                backgroundColor: "var(--color-green)",
              }}
            />
            <span>Feature</span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default GitBranchDiagram;
