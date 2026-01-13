import React from "react";
import { useSprings, animated } from "@react-spring/web";

interface AWSFlowDiagramProps {
  /** Optional deterministic sequence of characters (e.g., ['0','1','1',…]).
   *  Pass this from getServerSideProps/getStaticProps so that SSR and CSR output match,
   *  preventing hydration warnings like "Text content does not match server‑rendered HTML." */
  chars?: string[];
}

const AWSFlowDiagram: React.FC<AWSFlowDiagramProps> = ({ chars }) => {
  // ═══ Shared helpers ════════════════════════════════════════
  const BIT_COUNT = 15;
  const TILT = 30; // ±30°
  const FADE = (p: number) => 1 - Math.abs((p % 100) - 50) / 50; // 0→1→0

  /* ─── Global animation knobs ────────────────────────────── */
  const PHASE_LEN = 500; // default travel time per leg
  const STAGGER = 50; // pause between legs
  const CYCLE_PAUSE = 200; // extra pause between storyboard loops (ms)
  const LAUNCH_STEP = 50; // per‑glyph trail spacing

  /**
   * Effective length of a phase = travel time + time for the last bit to launch.
   * (BIT_COUNT - 1) × launch accounts for the per‑glyph trail.
   */
  const phaseLength = React.useCallback(
    (p: Phase) =>
      (p.duration ?? PHASE_LEN) + (BIT_COUNT - 1) * (p.launch ?? LAUNCH_STEP),
    [PHASE_LEN, BIT_COUNT, LAUNCH_STEP]
  );

  /* One logical "leg" in the choreography */
  type Phase = {
    paths: (keyof typeof PATH_REFS)[];
    dir: 1 | -1;
    duration?: number;
    launch?: number;
  };

  /* Storyboard: Mac ↔ AWS bidirectional flow */
  const TIMELINE = React.useMemo<Phase[]>(
    () => [
      { paths: ["TopMiddle"], dir: 1 },  // 1  Mac → AWS (request/upload)
      { paths: ["TopMiddle"], dir: -1 }, // 2  AWS → Mac (response/download)
    ],
    []
  );

  /* ─── Auto‑restart after one full storyboard ───────────── */
  const [cycle, setCycle] = React.useState(0);

  React.useEffect(() => {
    // total storyboard duration = sum(durations) + STAGGER between phases + CYCLE_PAUSE after last
    const totalMs =
      TIMELINE.reduce((acc, p) => acc + phaseLength(p) + STAGGER, 0) +
      CYCLE_PAUSE;

    const id = setTimeout(() => setCycle((c) => c + 1), totalMs);
    return () => clearTimeout(id);
  }, [TIMELINE, cycle, phaseLength]);

  /* Compute cumulative delay for a phase index */
  const delayFor = (idx: number) => {
    const delay = TIMELINE.slice(0, idx).reduce(
      (acc, p) => acc + phaseLength(p) + STAGGER,
      0
    );
    return delay;
  };

  type Bit = { char: "0" | "1"; rot: number; pathIdx: number };

  // create 5 refs for a fan‑out group
  const makeRefs = () =>
    Array.from({ length: 5 }, () => React.createRef<SVGPathElement>());

  const PATH_REFS = React.useMemo(
    () => ({
      TopMiddle: makeRefs() as React.RefObject<SVGPathElement>[],
    }),
    []
  );

  // get (x,y) point on path at pct%
  const pointAt = (ref: React.RefObject<SVGPathElement>, pct: number) => {
    const el = ref.current;
    if (!el) return { x: 0, y: 0 };
    const len = el.getTotalLength();
    return el.getPointAtLength(((pct % 100) / 100) * len);
  };

  // reusable bit stream component
  function BitStream({
    pathRefs,
    count = BIT_COUNT,
    duration = 5000,
    dir = -1,
    launch = 250,
    initialDelay = 0,
    chars,
  }: {
    pathRefs: React.RefObject<SVGPathElement>[];
    count?: number;
    duration?: number;
    dir?: 1 | -1;
    launch?: number;
    initialDelay?: number;
    chars?: string[];
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
      config: { duration, precision: 1, easing: (t: number) => t },
      delay: initialDelay + i * launch,
    }))[0];

    return (
      <>
        {springs.map((spring, i) => (
          <animated.g
            key={i}
            transform={spring.offset.to((o) => {
              const { x, y } = pointAt(pathRefs[bits[i].pathIdx], o);
              return `translate(${x},${y}) rotate(${bits[i].rot})`;
            })}
            opacity={spring.offset.to(FADE)}
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

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        marginTop: "1em",
        marginBottom: "1em",
      }}
    >
      <div>
        <svg height="178" width="300" viewBox="0 0 300 177.65">
          {/* MacBook / Laptop */}
          <g id="PyTorch">
            <path
              d="M204.49,71.69h0V5.74c0-3.17-2.57-5.74-5.74-5.74h-97.5c-3.17,0-5.74,2.57-5.74,5.74v65.96h-11.47v3.48c0,2.13,1.72,3.69,3.85,3.69h6.81l.62,1.43h7.37l.62-1.43h92.57l.62,1.43h7.37l.61-1.43h7.62c2.13,0,3.85-1.57,3.85-3.69v-3.48h-11.47ZM99.82,71.69V5.74c0-.79.64-1.43,1.43-1.43h41.58v.17c0,1.49,1.21,2.7,2.7,2.7h8.95c1.49,0,2.7-1.21,2.7-2.7v-.17h41.58c.79,0,1.43.64,1.43,1.43v65.96h-100.37Z"
              fill="var(--text-color)"
            />
          </g>

          {/* AWS Logo */}
          <g id="AWS">
            <g>
              {/* AWS text */}
              <path
                d="M129.82,142.43c0,1.14.12,2.06.34,2.74.25.68.55,1.41.98,2.21.15.25.22.49.22.71,0,.31-.18.62-.58.92l-1.94,1.29c-.28.18-.55.28-.8.28-.31,0-.62-.15-.92-.43-.43-.46-.8-.95-1.11-1.45-.31-.52-.62-1.11-.95-1.81-2.4,2.83-5.41,4.24-9.04,4.24-2.58,0-4.64-.74-6.15-2.21-1.51-1.48-2.28-3.44-2.28-5.9,0-2.61.92-4.74,2.8-6.34,1.88-1.6,4.37-2.4,7.53-2.4,1.05,0,2.12.09,3.26.25s2.31.4,3.54.68v-2.25c0-2.34-.49-3.97-1.45-4.92-.98-.95-2.64-1.41-5.01-1.41-1.08,0-2.18.12-3.32.40s-2.25.62-3.32,1.05c-.49.22-.86.34-1.08.4-.22.06-.37.09-.49.09-.43,0-.65-.31-.65-.95v-1.51c0-.49.06-.86.22-1.08s.43-.43.86-.65c1.08-.55,2.37-1.01,3.88-1.38,1.51-.4,3.11-.58,4.8-.58,3.66,0,6.34.83,8.06,2.49,1.69,1.66,2.55,4.18,2.55,7.57v9.96h.06ZM117.34,147.11c1.01,0,2.06-.18,3.17-.55,1.11-.37,2.09-1.05,2.92-1.97.49-.58.86-1.23,1.05-1.97s.31-1.63.31-2.68v-1.29c-.89-.22-1.85-.40-2.83-.52s-1.94-.18-2.89-.18c-2.06,0-3.57.40-4.58,1.23-1.01.83-1.51,2-1.51,3.54,0,1.45.37,2.52,1.14,3.26.74.77,1.81,1.14,3.23,1.14ZM142.03,150.43c-.55,0-.92-.09-1.17-.31-.25-.18-.46-.62-.65-1.2l-7.23-23.77c-.18-.62-.28-1.01-.28-1.23,0-.49.25-.77.74-.77h3.01c.58,0,.98.09,1.2.31.25.18.43.62.62,1.2l5.17,20.36,4.8-20.36c.15-.62.34-1.01.58-1.2.25-.18.68-.31,1.23-.31h2.46c.58,0,.98.09,1.23.31.25.18.46.62.58,1.2l4.86,20.61,5.32-20.61c.18-.62.40-1.01.62-1.2.25-.18.65-.31,1.2-.31h2.86c.49,0,.77.25.77.77,0,.15-.03.31-.06.49-.03.18-.09.43-.22.77l-7.41,23.77c-.18.62-.40,1.01-.65,1.2s-.65.31-1.17.31h-2.64c-.58,0-.98-.09-1.23-.31s-.46-.62-.58-1.23l-4.77-19.84-4.74,19.81c-.15.62-.34,1.01-.58,1.23-.25.22-.68.31-1.23.31h-2.64ZM181.55,151.26c-1.6,0-3.2-.18-4.74-.55-1.54-.37-2.74-.77-3.54-1.23-.49-.28-.83-.58-.95-.86-.12-.28-.18-.58-.18-.86v-1.57c0-.65.25-.95.71-.95.18,0,.37.03.55.09.18.06.46.18.77.31,1.05.46,2.18.83,3.38,1.08,1.23.25,2.43.37,3.66.37,1.94,0,3.44-.34,4.49-1.01,1.05-.68,1.6-1.66,1.6-2.92,0-.86-.28-1.57-.83-2.15-.55-.58-1.6-1.11-3.11-1.6l-4.46-1.38c-2.25-.71-3.91-1.75-4.92-3.14-1.01-1.35-1.54-2.86-1.54-4.46,0-1.29.28-2.43.83-3.41.55-.98,1.29-1.85,2.21-2.52.92-.71,1.97-1.23,3.2-1.6,1.23-.37,2.52-.52,3.88-.52.68,0,1.38.03,2.06.12.71.09,1.35.22,2,.34.62.15,1.2.31,1.75.49.55.18.98.37,1.29.55.43.25.74.49.92.77.18.25.28.58.28,1.01v1.45c0,.65-.25.98-.71.98-.25,0-.65-.12-1.17-.37-1.75-.80-3.72-1.2-5.9-1.2-1.75,0-3.14.28-4.09.86s-1.45,1.48-1.45,2.74c0,.86.31,1.6.92,2.18.62.58,1.75,1.17,3.38,1.69l4.37,1.38c2.21.71,3.81,1.69,4.77,2.95.95,1.26,1.41,2.71,1.41,4.31,0,1.32-.28,2.52-.80,3.57-.55,1.05-1.29,1.97-2.25,2.71-.95.77-2.09,1.32-3.41,1.72-1.38.43-2.83.65-4.40.65Z"
                fill="var(--text-color)"
              />
              {/* AWS smile/arrow */}
              <path
                d="M187.37,166.21c-10.12,7.47-24.82,11.44-37.46,11.44-17.71,0-33.68-6.55-45.73-17.44-.95-.86-.09-2.03,1.05-1.35,13.04,7.57,29.12,12.15,45.76,12.15,11.23,0,23.56-2.34,34.91-7.14,1.69-.77,3.14,1.11,1.48,2.34Z"
                fill="#f8991d"
                fillRule="evenodd"
              />
              <path
                d="M191.58,161.41c-1.29-1.66-8.55-.80-11.84-.40-.98.12-1.14-.74-.25-1.38,5.78-4.06,15.29-2.89,16.39-1.54,1.11,1.38-.31,10.89-5.72,15.44-.83.71-1.63.34-1.26-.58,1.23-3.04,3.97-9.90,2.68-11.53Z"
                fill="#f8991d"
                fillRule="evenodd"
              />
            </g>
          </g>

          {/* Animation paths: Mac ↔ AWS */}
          <g id="TopMiddle">
            <path
              d="M150,142.57V35.07"
              fill="none"
              stroke="none"
              ref={PATH_REFS.TopMiddle[0]}
            />
            <path
              d="M150,142.57c0-13.13,1.64-36.16,1.64-49.29,0-22.71-1.64-35.51-1.64-58.21"
              fill="none"
              stroke="none"
              ref={PATH_REFS.TopMiddle[1]}
            />
            <path
              d="M150,142.57c0-13.13-1.64-36.16-1.64-49.29,0-22.71,1.64-35.51,1.64-58.21"
              fill="none"
              stroke="none"
              ref={PATH_REFS.TopMiddle[2]}
            />
            <path
              d="M150,142.57c0-13.13,3.27-36.16,3.27-49.29,0-22.71-3.27-35.51-3.27-58.21"
              fill="none"
              stroke="none"
              ref={PATH_REFS.TopMiddle[3]}
            />
            <path
              d="M150,142.57c0-13.13-3.27-36.16-3.27-49.29,0-22.71,3.27-35.51,3.27-58.21"
              fill="none"
              stroke="none"
              ref={PATH_REFS.TopMiddle[4]}
            />
          </g>

          {/* --- animated 1/0 streams (per‑bit) --- */}
          <g
            id="bit-streams"
            key={cycle}
            fontFamily="monospace"
            fontSize="12"
          >
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
                />
              ))
            )}
          </g>
        </svg>
      </div>
    </div>
  );
};

export default AWSFlowDiagram;
