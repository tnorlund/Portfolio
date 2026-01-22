import React from "react";
import { useSprings, animated } from "@react-spring/web";
import styles from "./CodeBuildDiagram.module.css";
import {
  useViewportAnimation,
  pointAtCached,
  fadeLUT,
  OPTIMIZED_SPRING_CONFIG,
} from "./useDiagramOptimizations";

interface CodeBuildDiagramProps {
  /** Optional deterministic sequence of characters (e.g., ['0','1','1',…]).
   *  Pass this from getServerSideProps/getStaticProps so that SSR and CSR output match,
   *  preventing hydration warnings like "Text content does not match server‑rendered HTML." */
  chars?: string[];
  /** Optional: pause animation externally */
  paused?: boolean;
}

/**
 * Hook to detect if we're in mobile or desktop view.
 * Uses matchMedia for efficient breakpoint detection.
 * Returns 'mobile' during SSR to match CSS default.
 */
function useBreakpoint(breakpoint: number = 600): "mobile" | "desktop" {
  const [isMobile, setIsMobile] = React.useState(true); // Default to mobile for SSR

  React.useEffect(() => {
    // Check if matchMedia is available
    if (typeof window === "undefined" || !window.matchMedia) {
      return;
    }

    const mediaQuery = window.matchMedia(`(min-width: ${breakpoint}px)`);

    // Set initial value
    setIsMobile(!mediaQuery.matches);

    // Listen for changes
    const handler = (e: MediaQueryListEvent) => {
      setIsMobile(!e.matches);
    };

    mediaQuery.addEventListener("change", handler);
    return () => mediaQuery.removeEventListener("change", handler);
  }, [breakpoint]);

  return isMobile ? "mobile" : "desktop";
}

const CodeBuildDiagram: React.FC<CodeBuildDiagramProps> = ({ chars, paused = false }) => {
  // Viewport-aware animation control
  const { containerRef, shouldAnimate, springPause } = useViewportAnimation(paused);

  // Conditional rendering: only render mobile OR desktop layout, not both
  const layout = useBreakpoint(600);

  // ═══ Shared helpers ════════════════════════════════════════
  const BIT_COUNT = 15;
  const TILT = 30; // ±30°

  /* ─── Global animation knobs ────────────────────────────── */
  const PHASE_LEN = 500; // default travel time per leg
  const STAGGER = 50; // pause between legs
  const CYCLE_PAUSE = 200; // extra pause between storyboard loops (ms)
  const LAUNCH_STEP = 50; // per‑glyph trail spacing

  const phaseLength = React.useCallback(
    (p: Phase) =>
      (p.duration ?? PHASE_LEN) + (BIT_COUNT - 1) * (p.launch ?? LAUNCH_STEP),
    [PHASE_LEN, BIT_COUNT, LAUNCH_STEP]
  );

  type Phase = {
    paths: ("PulumiToS3" | "S3ToPipeline" | "PipelineToLambda")[];
    dir: 1 | -1;
    duration?: number;
    launch?: number;
  };

  const TIMELINE = React.useMemo<Phase[]>(
    () => [
      { paths: ["PulumiToS3"], dir: -1 },
      { paths: ["S3ToPipeline"], dir: -1 },
      { paths: ["PipelineToLambda"], dir: -1 },
    ],
    []
  );

  const [cycle, setCycle] = React.useState(0);

  React.useEffect(() => {
    if (!shouldAnimate) return;

    const totalMs =
      TIMELINE.reduce((acc, p) => acc + phaseLength(p) + STAGGER, 0) +
      CYCLE_PAUSE;

    const id = setTimeout(() => setCycle((c) => c + 1), totalMs);
    return () => clearTimeout(id);
  }, [TIMELINE, cycle, phaseLength, shouldAnimate]);

  const delayFor = (idx: number) => {
    const delay = TIMELINE.slice(0, idx).reduce(
      (acc, p) => acc + phaseLength(p) + STAGGER,
      0
    );
    return delay;
  };

  type Bit = { char: "0" | "1"; rot: number; pathIdx: number };

  const makeRefs = () =>
    Array.from({ length: 5 }, () => React.createRef<SVGPathElement>());

  // Only create refs for the active layout (conditional rendering optimization)
  const PATH_REFS = React.useMemo(
    () => ({
      PulumiToS3: makeRefs() as React.RefObject<SVGPathElement>[],
      S3ToPipeline: makeRefs() as React.RefObject<SVGPathElement>[],
      PipelineToLambda: makeRefs() as React.RefObject<SVGPathElement>[],
    }),
    // Re-create refs when layout changes since paths are different
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [layout]
  );

  function BitStream({
    pathRefs,
    count = BIT_COUNT,
    duration = 5000,
    dir = -1,
    launch = 250,
    initialDelay = 0,
    chars,
    pause,
  }: {
    pathRefs: React.RefObject<SVGPathElement>[];
    count?: number;
    duration?: number;
    dir?: 1 | -1;
    launch?: number;
    initialDelay?: number;
    chars?: string[];
    pause?: boolean;
  }) {
    const bits = React.useMemo<Bit[]>(
      () =>
        Array.from({ length: count }, (_, idx) => ({
          char: (chars?.[idx % chars.length] ?? (idx % 2 === 0 ? "1" : "0")) as
            | "0"
            | "1",
          rot: ((idx * 7.3) % TILT) - TILT / 2,
          pathIdx: idx % pathRefs.length,
        })),
      [count, pathRefs.length, chars]
    );

    const springs = useSprings(bits.length, (i) => ({
      from: { offset: dir === -1 ? 100 : 0 },
      to: { offset: dir === -1 ? 0 : 100 },
      config: { duration, ...OPTIMIZED_SPRING_CONFIG },
      delay: initialDelay + i * launch,
      pause,
    }))[0];

    return (
      <>
        {springs.map((spring, i) => (
          <animated.g
            key={i}
            transform={spring.offset.to((o) => {
              const { x, y } = pointAtCached(pathRefs[bits[i].pathIdx], o);
              return `translate(${x},${y}) rotate(${bits[i].rot})`;
            })}
            opacity={spring.offset.to(fadeLUT)}
          >
            <rect
              x="-0.45em"
              y="-0.7em"
              width="0.9em"
              height="1.2em"
              fill="var(--code-background)"
            />
            <text
              dominantBaseline="middle"
              textAnchor="middle"
              fill="var(--text-color)"
            >
              {bits[i].char}
            </text>
          </animated.g>
        ))}
      </>
    );
  }

  // Vertical (mobile) SVG content
  const MobileSVG = () => (
    <svg height="400" width="300" viewBox="0 0 300 400">
      <defs>
        <linearGradient id="lambda-gradient" x1="107.49" y1="400.25" x2="167.59" y2="340.15" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#c85428" />
          <stop offset="1" stopColor="#f8981d" />
        </linearGradient>
        <linearGradient id="s3-gradient" x1="107.49" y1="185.25" x2="167.59" y2="125.15" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#1f6835" />
          <stop offset="1" stopColor="#6bad44" />
        </linearGradient>
        <linearGradient id="codepipeline-gradient" x1="107.49" y1="292.75" x2="167.59" y2="232.65" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#3b3f99" />
          <stop offset="1" stopColor="#5c76ba" />
        </linearGradient>
      </defs>

      {/* Pulumi logo */}
      <g>
        <path fill="#f2707e" fillRule="evenodd" d="M128.93,45.3c2.53-1.46,2.53-6.19,0-10.57-2.53-4.37-6.62-6.74-9.16-5.27-2.53,1.46-2.53,6.19,0,10.57,2.53,4.37,6.62,6.74,9.16,5.27ZM128.95,54.66c2.53,4.37,2.52,9.1,0,10.57-2.53,1.46-6.63-.9-9.16-5.27-2.53-4.37-2.52-9.1,0-10.57,2.53-1.46,6.63.9,9.16,5.27ZM146.2,64.64c2.53,4.37,2.52,9.1,0,10.57-2.53,1.46-6.63-.9-9.16-5.28-2.53-4.37-2.52-9.1,0-10.57,2.53-1.46,6.63.9,9.16,5.27ZM146.19,44.7c2.53,4.37,2.52,9.1,0,10.57-2.53,1.46-6.63-.9-9.16-5.27-2.53-4.37-2.52-9.1,0-10.57,2.53-1.46,6.63.9,9.16,5.27Z" />
        <path fill="#8b3591" fillRule="evenodd" d="M180.69,40.03c2.53-4.37,2.52-9.1,0-10.57-2.53-1.46-6.63.9-9.16,5.27-2.53,4.37-2.52,9.1,0,10.57,2.53,1.46,6.63-.9,9.16-5.27ZM180.69,49.4c2.53,1.46,2.54,6.19,0,10.57-2.53,4.37-6.62,6.74-9.16,5.27s-2.53-6.19,0-10.57c2.53-4.37,6.62-6.74,9.16-5.27ZM163.45,59.35c2.53,1.46,2.54,6.19,0,10.57-2.53,4.37-6.62,6.74-9.16,5.27-2.53-1.46-2.53-6.19,0-10.57,2.53-4.37,6.62-6.74,9.16-5.27ZM163.44,39.42c2.53,1.46,2.53,6.19,0,10.57-2.53,4.37-6.62,6.74-9.16,5.27-2.53-1.46-2.53-6.19,0-10.57,2.53-4.37,6.62-6.74,9.16-5.27Z" />
        <path fill="#f6bf28" fillRule="evenodd" d="M159.38,12.5c0,2.92-4.09,5.29-9.15,5.29s-9.15-2.37-9.15-5.29,4.09-5.29,9.15-5.29,9.15,2.37,9.15,5.29ZM142.14,22.44c0,2.92-4.09,5.29-9.15,5.29s-9.15-2.37-9.15-5.29,4.09-5.29,9.15-5.29,9.15,2.37,9.15,5.29ZM167.5,27.73c5.05,0,9.15-2.37,9.15-5.29s-4.09-5.29-9.15-5.29-9.15,2.37-9.15,5.29,4.09,5.29,9.15,5.29ZM159.38,32.41c0,2.92-4.09,5.29-9.15,5.29s-9.15-2.37-9.15-5.29,4.09-5.29,9.15-5.29,9.15,2.37,9.15,5.29Z" />
      </g>

      {/* S3 icon */}
      <g>
        <rect x="107.74" y="100" width="85" height="85" fill="url(#s3-gradient)" />
        <g>
          <path fill="white" fillRule="evenodd" d="M148.62,130.92c-13.67,0-27.52-3.12-27.52-9.09s13.84-9.09,27.52-9.09,27.52,3.12,27.52,9.09-13.84,9.09-27.52,9.09ZM148.62,115.22c-15.51,0-25.04,3.85-25.04,6.61s9.53,6.61,25.04,6.61,25.04-3.85,25.04-6.61-9.53-6.61-25.04-6.61Z" />
          <path fill="white" fillRule="evenodd" d="M148.62,172.3c-7.7,0-15.07-1.44-20.22-3.96-.37-.18-.62-.53-.68-.94l-6.61-45.39,2.45-.36,6.51,44.73c4.79,2.18,11.5,3.43,18.54,3.43s13.75-1.25,18.54-3.43l6.51-44.73,2.45.36-6.6,45.39c-.06.41-.32.76-.68.94-5.15,2.52-12.52,3.96-20.22,3.96Z" />
          <g><circle fill="white" cx="148.64" cy="136.84" r="1.64" /><path fill="white" fillRule="evenodd" d="M148.64,139.72c-1.59,0-2.88-1.29-2.88-2.88s1.29-2.88,2.88-2.88,2.88,1.29,2.88,2.88-1.29,2.88-2.88,2.88ZM148.64,136.44c-.22,0-.4.18-.4.4s.18.4.4.4.4-.18.4-.4-.18-.4-.4-.4Z" /></g>
          <path fill="white" fillRule="evenodd" d="M176.53,148.16c-6.16,0-23.59-6.92-28.58-10.3l1.39-2.05c5.99,4.06,22.57,9.83,26.9,9.94-.59-.63-1.9-1.8-4.82-3.77l1.39-2.05c5.4,3.65,6.45,5.22,6.37,6.47-.05.65-.43,1.19-1.05,1.49-.37.18-.92.26-1.59.26Z" />
        </g>
      </g>

      {/* CodePipeline icon */}
      <g>
        <rect x="107.74" y="207.5" width="85" height="85" fill="url(#codepipeline-gradient)" />
        <g>
          <path fill="white" fillRule="evenodd" d="M139.62,240.44h5.31v-2.12h-5.31v2.12ZM146.7,272.49l-1.97-.81,8.07-19.68,1.97.81-8.07,19.68ZM156.16,266.77l5.84-5.12-5.84-5.06,1.39-1.61,6.76,5.86c.23.2.37.49.37.8,0,.31-.13.6-.36.8l-6.76,5.93-1.4-1.6ZM134.84,261.74c0-.31.13-.6.36-.8l6.73-5.95,1.41,1.59-5.82,5.15,5.79,4.99-1.39,1.61-6.71-5.79c-.23-.2-.37-.49-.37-.8h0ZM174.18,244.69h-46.82c-2.03,0-3.68-1.65-3.68-3.68v-.57h12.75v-2.12h-12.75v-11.19c0-2.03,1.65-3.68,3.68-3.68h46.82c2.03,0,3.68,1.65,3.68,3.68v11.19h-29.75v2.12h29.75v.57c0,2.03-1.65,3.68-3.68,3.68h0ZM131.12,277.63h38.25v-30.81h-38.25v30.81ZM174.18,221.31h-46.82c-3.2,0-5.81,2.6-5.81,5.81v13.89c0,3.2,2.6,5.81,5.81,5.81h1.63v31.88c0,.59.47,1.06,1.06,1.06h40.38c.59,0,1.06-.48,1.06-1.06v-31.88h2.69c3.2,0,5.81-2.6,5.81-5.81v-13.89c0-3.2-2.6-5.81-5.81-5.81h0Z" />
        </g>
      </g>

      {/* Lambda icon */}
      <g>
        <rect x="107.74" y="315" width="85" height="85" fill="url(#lambda-gradient)" />
        <g>
          <path fill="white" fillRule="evenodd" d="M138.03,387.64h-15.5c-.43,0-.82-.22-1.05-.58-.23-.36-.25-.81-.07-1.2l16.31-34.08c.2-.43.63-.7,1.11-.7h.01c.47,0,.9.27,1.11.68l7.89,15.77c.17.34.18.74.01,1.09l-8.69,18.31c-.21.43-.64.71-1.12.71ZM124.5,385.16h12.75l8.09-17.06-6.48-12.96-14.36,30.02Z" />
          <path fill="white" fillRule="evenodd" d="M177.73,387.64h-14.57c-.48,0-.91-.27-1.12-.7l-21.31-44.34h-8.71c-.68,0-1.24-.55-1.24-1.24v-12.56c0-.68.55-1.24,1.24-1.24h18.71c.48,0,.92.28,1.12.71l20.78,44.07h5.1c.68,0,1.24.55,1.24,1.24v12.83c0,.68-.55,1.24-1.24,1.24ZM163.94,385.16h12.55v-10.35h-4.64c-.48,0-.92-.28-1.12-.71l-20.78-44.07h-16.69v10.08h8.25c.48,0,.91.27,1.12.7l21.31,44.34Z" />
        </g>
      </g>

      {/* Vertical paths */}
      <g id="Pulumi_S3">
        <path d="M150.24,146.5V39" fill="none" stroke="none" ref={PATH_REFS.PulumiToS3[0]} />
        <path d="M150.24,146.5c0-13.13,1.64-36.16,1.64-49.29,0-22.71-1.64-35.51-1.64-58.21" fill="none" stroke="none" ref={PATH_REFS.PulumiToS3[1]} />
        <path d="M150.24,146.5c0-13.13-1.64-36.16-1.64-49.29,0-22.71,1.64-35.51,1.64-58.21" fill="none" stroke="none" ref={PATH_REFS.PulumiToS3[2]} />
        <path d="M150.24,146.5c0-13.13,3.27-36.16,3.27-49.29,0-22.71-3.27-35.51-3.27-58.21" fill="none" stroke="none" ref={PATH_REFS.PulumiToS3[3]} />
        <path d="M150.24,146.5c0-13.13-3.27-36.16-3.27-49.29,0-22.71,3.27-35.51,3.27-58.21" fill="none" stroke="none" ref={PATH_REFS.PulumiToS3[4]} />
      </g>
      <g id="S3_Codebuild">
        <path d="M150.24,253.75v-107.5" fill="none" stroke="none" ref={PATH_REFS.S3ToPipeline[0]} />
        <path d="M150.24,253.75c0-13.13,1.64-36.16,1.64-49.29,0-22.71-1.64-35.51-1.64-58.21" fill="none" stroke="none" ref={PATH_REFS.S3ToPipeline[1]} />
        <path d="M150.24,253.75c0-13.13-1.64-36.16-1.64-49.29,0-22.71,1.64-35.51,1.64-58.21" fill="none" stroke="none" ref={PATH_REFS.S3ToPipeline[2]} />
        <path d="M150.24,253.75c0-13.13,3.27-36.16,3.27-49.29,0-22.71-3.27-35.51-3.27-58.21" fill="none" stroke="none" ref={PATH_REFS.S3ToPipeline[3]} />
        <path d="M150.24,253.75c0-13.13-3.27-36.16-3.27-49.29,0-22.71,3.27-35.51,3.27-58.21" fill="none" stroke="none" ref={PATH_REFS.S3ToPipeline[4]} />
      </g>
      <g id="Codebuild_lambda">
        <path d="M150.24,361.25v-107.5" fill="none" stroke="none" ref={PATH_REFS.PipelineToLambda[0]} />
        <path d="M150.24,361.25c0-13.13,1.64-36.16,1.64-49.29,0-22.71-1.64-35.51-1.64-58.21" fill="none" stroke="none" ref={PATH_REFS.PipelineToLambda[1]} />
        <path d="M150.24,361.25c0-13.13-1.64-36.16-1.64-49.29,0-22.71,1.64-35.51,1.64-58.21" fill="none" stroke="none" ref={PATH_REFS.PipelineToLambda[2]} />
        <path d="M150.24,361.25c0-13.13,3.27-36.16,3.27-49.29,0-22.71-3.27-35.51-3.27-58.21" fill="none" stroke="none" ref={PATH_REFS.PipelineToLambda[3]} />
        <path d="M150.24,361.25c0-13.13-3.27-36.16-3.27-49.29,0-22.71,3.27-35.51,3.27-58.21" fill="none" stroke="none" ref={PATH_REFS.PipelineToLambda[4]} />
      </g>

      <g id="bit-streams" key={cycle} fontFamily="monospace" fontSize="12">
        {TIMELINE.map((phase, phaseIdx) =>
          phase.paths.map((name) => (
            <BitStream
              key={`${phaseIdx}-${name}`}
              pathRefs={PATH_REFS[name]}
              dir={phase.dir}
              duration={phase.duration ?? PHASE_LEN}
              launch={phase.launch ?? LAUNCH_STEP}
              initialDelay={delayFor(phaseIdx)}
              chars={chars}
              pause={springPause}
            />
          ))
        )}
      </g>
    </svg>
  );

  // Horizontal (desktop) SVG content
  const DesktopSVG = () => (
    <svg height="200" width="400" viewBox="0 0 400 200">
      <defs>
        <linearGradient id="lambda-gradient-h" x1="310.32" y1="142.64" x2="370.43" y2="82.54" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#c85428" />
          <stop offset="1" stopColor="#f8981d" />
        </linearGradient>
        <linearGradient id="s3-gradient-h" x1="95.32" y1="142.64" x2="155.43" y2="82.54" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#1f6835" />
          <stop offset="1" stopColor="#6bad44" />
        </linearGradient>
        <linearGradient id="codepipeline-gradient-h" x1="202.82" y1="142.64" x2="262.93" y2="82.54" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#3b3f99" />
          <stop offset="1" stopColor="#5c76ba" />
        </linearGradient>
      </defs>

      {/* Pulumi logo */}
      <g>
        <path fill="#f2707e" fillRule="evenodd" d="M15.58,103.88c2.53-1.46,2.53-6.19,0-10.57-2.53-4.37-6.62-6.74-9.16-5.27-2.53,1.46-2.53,6.19,0,10.57,2.53,4.37,6.62,6.74,9.16,5.27ZM15.6,113.24c2.53,4.37,2.52,9.1,0,10.57-2.53,1.46-6.63-.9-9.16-5.27-2.53-4.37-2.52-9.1,0-10.57,2.53-1.46,6.63.9,9.16,5.27ZM32.84,123.22c2.53,4.37,2.52,9.1,0,10.57-2.53,1.46-6.63-.9-9.16-5.28-2.53-4.37-2.52-9.1,0-10.57,2.53-1.46,6.63.9,9.16,5.27ZM32.84,103.28c2.53,4.37,2.52,9.1,0,10.57-2.53,1.46-6.63-.9-9.16-5.27-2.53-4.37-2.52-9.1,0-10.57,2.53-1.46,6.63.9,9.16,5.27Z" />
        <path fill="#8b3591" fillRule="evenodd" d="M67.34,98.61c2.53-4.37,2.52-9.1,0-10.57-2.53-1.46-6.63.9-9.16,5.27-2.53,4.37-2.52,9.1,0,10.57,2.53,1.46,6.63-.9,9.16-5.27ZM67.34,107.98c2.53,1.46,2.54,6.19,0,10.57-2.53,4.37-6.62,6.74-9.16,5.27s-2.53-6.19,0-10.57c2.53-4.37,6.62-6.74,9.16-5.27ZM50.1,117.93c2.53,1.46,2.54,6.19,0,10.57-2.53,4.37-6.62,6.74-9.16,5.27-2.53-1.46-2.53-6.19,0-10.57,2.53-4.37,6.62-6.74,9.16-5.27ZM50.09,98c2.53,1.46,2.53,6.19,0,10.57-2.53,4.37-6.62,6.74-9.16,5.27-2.53-1.46-2.53-6.19,0-10.57,2.53-4.37,6.62-6.74,9.16-5.27Z" />
        <path fill="#f6bf28" fillRule="evenodd" d="M46.02,71.08c0,2.92-4.09,5.29-9.15,5.29s-9.15-2.37-9.15-5.29,4.09-5.29,9.15-5.29,9.15,2.37,9.15,5.29ZM28.78,81.02c0,2.92-4.09,5.29-9.15,5.29s-9.15-2.37-9.15-5.29c0-2.92,4.09-5.29,9.15-5.29s9.15,2.37,9.15,5.29ZM54.14,86.31c5.05,0,9.15-2.37,9.15-5.29s-4.09-5.29-9.15-5.29-9.15,2.37-9.15,5.29,4.09,5.29,9.15,5.29ZM46.02,90.99c0,2.92-4.09,5.29-9.15,5.29s-9.15-2.37-9.15-5.29,4.09-5.29,9.15-5.29,9.15,2.37,9.15,5.29Z" />
      </g>

      {/* S3 icon */}
      <g>
        <rect x="95.47" y="57.5" width="85" height="85" fill="url(#s3-gradient-h)" />
        <g>
          <path fill="white" fillRule="evenodd" d="M136.34,88.42c-13.67,0-27.52-3.12-27.52-9.09s13.84-9.09,27.52-9.09c13.67,0,27.52,3.12,27.52,9.09s-13.84,9.09-27.52,9.09ZM136.34,72.72c-15.51,0-25.04,3.85-25.04,6.61s9.53,6.61,25.04,6.61,25.04-3.85,25.04-6.61c0-2.76-9.53-6.61-25.04-6.61Z" />
          <path fill="white" fillRule="evenodd" d="M136.34,129.8c-7.7,0-15.07-1.44-20.22-3.96-.37-.18-.62-.53-.68-.94l-6.61-45.39,2.45-.36,6.51,44.73c4.79,2.18,11.5,3.43,18.54,3.43,7.04,0,13.75-1.25,18.54-3.43l6.51-44.73,2.45.36-6.6,45.39c-.06.41-.32.76-.68.94-5.15,2.52-12.52,3.96-20.22,3.96Z" />
          <g><circle fill="white" cx="136.37" cy="94.34" r="1.64" /><path fill="white" fillRule="evenodd" d="M136.37,97.22c-1.59,0-2.88-1.29-2.88-2.88s1.29-2.88,2.88-2.88,2.88,1.29,2.88,2.88-1.29,2.88-2.88,2.88ZM136.37,93.94c-.22,0-.4.18-.4.4s.18.4.4.4.4-.18.4-.4-.18-.4-.4-.4Z" /></g>
          <path fill="white" fillRule="evenodd" d="M164.25,105.66c-6.16,0-23.59-6.92-28.58-10.3l1.39-2.05c5.99,4.06,22.57,9.83,26.9,9.94-.59-.63-1.9-1.8-4.82-3.77l1.39-2.05c5.4,3.65,6.45,5.22,6.37,6.47-.05.65-.43,1.19-1.05,1.49-.37.18-.92.26-1.59.26Z" />
        </g>
      </g>

      {/* CodePipeline icon */}
      <g>
        <rect x="202.97" y="57.5" width="85" height="85" fill="url(#codepipeline-gradient-h)" />
        <g>
          <path fill="white" fillRule="evenodd" d="M234.84,90.44h5.31v-2.13h-5.31v2.13ZM241.92,122.49l-1.97-.81,8.07-19.68,1.97.81-8.07,19.68ZM251.39,116.77l5.84-5.12-5.84-5.06,1.39-1.61,6.76,5.86c.23.2.37.49.37.8,0,.31-.13.6-.36.8l-6.76,5.93-1.4-1.6ZM230.07,111.74c0-.31.13-.6.36-.8l6.73-5.95,1.41,1.59-5.82,5.15,5.79,4.99-1.39,1.61-6.71-5.79c-.23-.2-.37-.49-.37-.8h0ZM269.41,94.69h-46.82c-2.03,0-3.68-1.65-3.68-3.68v-.57s12.75,0,12.75,0v-2.12h-12.75s0-11.19,0-11.19c0-2.03,1.65-3.68,3.68-3.68h46.82c2.03,0,3.68,1.65,3.68,3.68v11.19s-29.75,0-29.75,0v2.12h29.75s0,.57,0,.57c0,2.03-1.65,3.68-3.68,3.68h0ZM226.34,127.63h38.25s0-30.81,0-30.81h-38.25s0,30.81,0,30.81ZM269.41,71.31h-46.82c-3.2,0-5.81,2.6-5.81,5.81v13.89c0,3.2,2.6,5.81,5.81,5.81h1.63s0,31.87,0,31.87c0,.59.47,1.06,1.06,1.06h40.37c.59,0,1.06-.48,1.06-1.06v-31.87s2.69,0,2.69,0c3.2,0,5.81-2.6,5.81-5.81v-13.89c0-3.2-2.6-5.81-5.81-5.81h0Z" />
        </g>
      </g>

      {/* Lambda icon */}
      <g>
        <rect x="310.47" y="57.5" width="85" height="85" fill="url(#lambda-gradient-h)" />
        <g>
          <path fill="white" fillRule="evenodd" d="M340.76,130.14h-15.5c-.43,0-.82-.22-1.05-.58-.23-.36-.25-.81-.07-1.2l16.31-34.08c.2-.43.63-.7,1.11-.7h.01c.47,0,.9.27,1.11.68l7.89,15.77c.17.34.18.74.01,1.09l-8.69,18.31c-.21.43-.64.71-1.12.71ZM327.22,127.66h12.75s8.09-17.06,8.09-17.06l-6.48-12.96-14.36,30.02Z" />
          <path fill="white" fillRule="evenodd" d="M380.46,130.14h-14.57c-.48,0-.91-.27-1.12-.7l-21.31-44.34h-8.71c-.68,0-1.24-.55-1.24-1.24v-12.56c0-.68.55-1.24,1.24-1.24h18.71c.48,0,.92.28,1.12.71l20.78,44.07h5.1c.68,0,1.24.55,1.24,1.24v12.83c0,.68-.55,1.24-1.24,1.24ZM366.67,127.66h12.55s0-10.35,0-10.35h-4.64c-.48,0-.92-.28-1.12-.71l-20.78-44.07h-16.69s0,10.08,0,10.08h8.25c.48,0,.91.27,1.12.7l21.31,44.34Z" />
        </g>
      </g>

      {/* Horizontal paths */}
      <g id="Pulumi_S3_H">
        <path d="M34.46,100 L141.96,100" fill="none" stroke="none" ref={PATH_REFS.PulumiToS3[0]} />
        <path d="M34.46,100c13.13,0,36.16-1.64,49.29-1.64,22.71,0,35.51,1.64,58.21,1.64" fill="none" stroke="none" ref={PATH_REFS.PulumiToS3[1]} />
        <path d="M34.46,100c13.13,0,36.16,1.64,49.29,1.64,22.71,0,35.51-1.64,58.21-1.64" fill="none" stroke="none" ref={PATH_REFS.PulumiToS3[2]} />
        <path d="M34.46,100c13.13,0,36.16-3.27,49.29-3.27,22.71,0,35.51,3.27,58.21,3.27" fill="none" stroke="none" ref={PATH_REFS.PulumiToS3[3]} />
        <path d="M34.46,100c13.13,0,36.16,3.27,49.29,3.27,22.71,0,35.51-3.27,58.21-3.27" fill="none" stroke="none" ref={PATH_REFS.PulumiToS3[4]} />
      </g>
      <g id="S3_Pipeline_H">
        <path d="M141.96,100 L249.22,100" fill="none" stroke="none" ref={PATH_REFS.S3ToPipeline[0]} />
        <path d="M141.96,100c13.13,0,36.16-1.64,49.29-1.64,22.71,0,35.51,1.64,58.21,1.64" fill="none" stroke="none" ref={PATH_REFS.S3ToPipeline[1]} />
        <path d="M141.96,100c13.13,0,36.16,1.64,49.29,1.64,22.71,0,35.51-1.64,58.21-1.64" fill="none" stroke="none" ref={PATH_REFS.S3ToPipeline[2]} />
        <path d="M141.96,100c13.13,0,36.16-3.27,49.29-3.27,22.71,0,35.51,3.27,58.21,3.27" fill="none" stroke="none" ref={PATH_REFS.S3ToPipeline[3]} />
        <path d="M141.96,100c13.13,0,36.16,3.27,49.29,3.27,22.71,0,35.51-3.27,58.21-3.27" fill="none" stroke="none" ref={PATH_REFS.S3ToPipeline[4]} />
      </g>
      <g id="Pipeline_Lambda_H">
        <path d="M249.22,100 L356.72,100" fill="none" stroke="none" ref={PATH_REFS.PipelineToLambda[0]} />
        <path d="M249.22,100c13.13,0,36.16-1.64,49.29-1.64,22.71,0,35.51,1.64,58.21,1.64" fill="none" stroke="none" ref={PATH_REFS.PipelineToLambda[1]} />
        <path d="M249.22,100c13.13,0,36.16,1.64,49.29,1.64,22.71,0,35.51-1.64,58.21-1.64" fill="none" stroke="none" ref={PATH_REFS.PipelineToLambda[2]} />
        <path d="M249.22,100c13.13,0,36.16-3.27,49.29-3.27,22.71,0,35.51,3.27,58.21,3.27" fill="none" stroke="none" ref={PATH_REFS.PipelineToLambda[3]} />
        <path d="M249.22,100c13.13,0,36.16,3.27,49.29,3.27,22.71,0,35.51-3.27,58.21-3.27" fill="none" stroke="none" ref={PATH_REFS.PipelineToLambda[4]} />
      </g>

      <g id="bit-streams-h" key={cycle} fontFamily="monospace" fontSize="12">
        {TIMELINE.map((phase, phaseIdx) =>
          phase.paths.map((name) => (
            <BitStream
              key={`h-${phaseIdx}-${name}`}
              pathRefs={PATH_REFS[name]}
              dir={1}
              duration={phase.duration ?? PHASE_LEN}
              launch={phase.launch ?? LAUNCH_STEP}
              initialDelay={delayFor(phaseIdx)}
              chars={chars}
              pause={springPause}
            />
          ))
        )}
      </g>
    </svg>
  );

  return (
    <div
      ref={containerRef}
      style={{
        display: "flex",
        justifyContent: "center",
        marginTop: "1em",
        marginBottom: "1em",
      }}
    >
      <div>
        {/* Conditional rendering: only render active layout, not both */}
        {layout === "mobile" ? (
          <div className={styles["mobile-only"]}>
            <MobileSVG />
          </div>
        ) : (
          <div className={styles["desktop-only"]}>
            <DesktopSVG />
          </div>
        )}
      </div>
    </div>
  );
};

export default CodeBuildDiagram;
