import React from "react";
import { useSprings, animated } from "@react-spring/web";

interface UploadDiagramProps {
  /** Optional deterministic sequence of characters (e.g., ['0','1','1',…]).
   *  Pass this from getServerSideProps/getStaticProps so that SSR and CSR output match,
   *  preventing hydration warnings like "Text content does not match server‑rendered HTML." */
  chars?: string[];
}

const UploadDiagram: React.FC<UploadDiagramProps> = ({ chars }) => {
  // ═══ Shared helpers ════════════════════════════════════════
  const BIT_COUNT = 15;
  const TILT = 30; // ±30°
  const FADE = (p: number) => 1 - Math.abs((p % 100) - 50) / 50; // 0→1→0

  /* ─── Global animation knobs ────────────────────────────── */
  const PHASE_LEN = 500; // default travel time per leg (reduced from 700)
  const STAGGER = 50; // pause between legs
  const CYCLE_PAUSE = 200; // extra pause between storyboard loops (ms)
  const LAUNCH_STEP = 50; // per‑glyph trail spacing (reduced from 75)

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

  /* Storyboard (edit order, timing, or direction here) */
  const TIMELINE = React.useMemo<Phase[]>(
    () => [
      { paths: ["TopMiddle"], dir: 1 }, // 1  SQS → Mac
      { paths: ["TopLeft", "TopRight"], dir: 1 }, // 2  L/R → Mac
      { paths: ["TopLeft", "TopRight"], dir: -1 }, // 3  Mac → L/R
      { paths: ["TopMiddle"], dir: -1 }, // 4  Mac → SQS
      { paths: ["BottomMiddle"], dir: -1 }, // 5  SQS → Lambda
      { paths: ["BottomLeft", "BottomRight"], dir: 1 }, // 6  L/R → Lambda
      { paths: ["BottomLeft", "BottomRight"], dir: -1 }, // 7  Lambda → L/R
      { paths: ["BottomMiddle"], dir: 1 }, // 8  Lambda → SQS
    ],
    []
  ); // Empty dependency array since TIMELINE is static

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

  const PATH_REFS = React.useMemo(() => {
    return {
      BottomMiddle: makeRefs(),
      BottomLeft: makeRefs(),
      BottomRight: makeRefs(),
      TopMiddle: makeRefs(),
      TopLeft: makeRefs(),
      TopRight: makeRefs(),
    };
  }, []); // Empty dependency - refs should never change

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
        <div>
          <div>
            <svg height="300" width="300" viewBox="0 0 300 300">
              <defs>
                <linearGradient
                  id="lambda-gradient"
                  x1="107.25"
                  y1="300.25"
                  x2="167.35"
                  y2="240.15"
                  gradientUnits="userSpaceOnUse"
                >
                  <stop offset="0" stopColor="#c85428" />
                  <stop offset="1" stopColor="#f8981d" />
                </linearGradient>
                <linearGradient
                  id="sqs-gradient"
                  x1="107.25"
                  y1="192.75"
                  x2="167.35"
                  y2="132.65"
                  gradientUnits="userSpaceOnUse"
                >
                  <stop offset="0" stopColor="#b01e50" />
                  <stop offset="1" stopColor="#ef518a" />
                </linearGradient>
                <linearGradient
                  id="dynamo-gradient"
                  x1="-.25"
                  y1="192.75"
                  x2="59.85"
                  y2="132.65"
                  gradientUnits="userSpaceOnUse"
                >
                  <stop offset="0" stopColor="#3b3f99" />
                  <stop offset="1" stopColor="#5c76ba" />
                </linearGradient>
                <linearGradient
                  id="linear-gradient-4"
                  x1="214.75"
                  y1="192.75"
                  x2="274.85"
                  y2="132.65"
                  gradientUnits="userSpaceOnUse"
                >
                  <stop offset="0" stopColor="#1f6835" />
                  <stop offset="1" stopColor="#6bad44" />
                </linearGradient>
              </defs>

              <g id="PyTorch">
                <path
                  d="M204.49,74.04h0V8.09c0-3.17-2.57-5.74-5.74-5.74h-97.5c-3.17,0-5.74,2.57-5.74,5.74v65.96h-11.47v3.48c0,2.13,1.72,3.69,3.85,3.69h6.81l.62,1.43h7.37l.62-1.43h92.57l.62,1.43h7.37l.61-1.43h7.62c2.13,0,3.85-1.57,3.85-3.69v-3.48h-11.47ZM99.81,74.04V8.09c0-.79.64-1.43,1.43-1.43h41.58v.17c0,1.49,1.21,2.7,2.7,2.7h8.95c1.49,0,2.7-1.21,2.7-2.7v-.17h41.58c.79,0,1.43.64,1.43,1.43v65.96h-100.37Z"
                  fill="var(--text-color)"
                />
              </g>
              <g>
                <rect
                  x="107.5"
                  y="215"
                  width="85"
                  height="85"
                  fill="url(#lambda-gradient)"
                />
                <g>
                  <path
                    d="M137.79,287.64h-15.5c-.43,0-.82-.22-1.05-.58-.23-.36-.25-.81-.07-1.2l16.31-34.08c.2-.43.63-.7,1.11-.7h.01c.47,0,.9.27,1.11.68l7.89,15.77c.17.34.18.74.01,1.09l-8.69,18.31c-.21.43-.64.71-1.12.71ZM124.26,285.16h12.75l8.09-17.06-6.48-12.96-14.36,30.02Z"
                    fill="white"
                  />
                  <path
                    d="M177.49,287.64h-14.57c-.48,0-.91-.27-1.12-.7l-21.31-44.34h-8.71c-.68,0-1.24-.55-1.24-1.24v-12.56c0-.68.55-1.24,1.24-1.24h18.71c.48,0,.92.28,1.12.71l20.78,44.07h5.1c.68,0,1.24.55,1.24,1.24v12.83c0,.68-.55,1.24-1.24,1.24ZM163.7,285.16h12.55v-10.35h-4.64c-.48,0-.92-.28-1.12-.71l-20.78-44.07h-16.69v10.08h8.25c.48,0,.91.27,1.12.7l21.31,44.34Z"
                    fill="white"
                  />
                </g>
              </g>

              <g>
                <rect
                  x="107.5"
                  y="107.5"
                  width="85"
                  height="85"
                  fill="url(#sqs-gradient)"
                />
                <g
                  id="Icon-Architecture_64_Arch_AWS-Simple-Queue-Service_64"
                  data-name="Icon-Architecture/64/Arch_AWS-Simple-Queue-Service_64"
                >
                  <path
                    id="AWS-Simple-Queue-Service_Icon_64_Squid"
                    data-name="AWS-Simple-Queue-Service Icon 64 Squid"
                    d="M137.35,153.89l3.19-3.15c.2-.2.31-.47.31-.75,0-.28-.11-.55-.31-.75l-3.19-3.19-1.51,1.49,1.37,1.37h-4.87v2.11h4.88l-1.38,1.36,1.5,1.5ZM161.69,153.98l4.25-3.17c.27-.2.42-.51.42-.84s-.16-.65-.43-.85l-4.25-3.17-1.28,1.69,1.7,1.27h-4.25v2.11h4.25l-1.7,1.27,1.28,1.69ZM144.25,149.97c0,2.24-.39,4.36-1.11,6.2,1.91-.74,4.07-1.1,6.22-1.1s4.31.37,6.22,1.1c-.72-1.84-1.11-3.96-1.11-6.2s.39-4.36,1.11-6.2c-3.82,1.47-8.62,1.47-12.45,0,.73,1.84,1.11,3.96,1.11,6.2h0ZM139.04,160.22c-.21-.21-.31-.48-.31-.75s.1-.54.31-.75c1.93-1.92,3.08-5.19,3.08-8.76s-1.15-6.84-3.08-8.76c-.21-.21-.31-.48-.31-.75s.1-.54.31-.75c.42-.41,1.09-.41,1.5,0,4.12,4.09,13.51,4.09,17.63,0,.42-.41,1.09-.41,1.5,0,.21.21.31.48.31.75s-.1.54-.31.75c-1.93,1.92-3.08,5.19-3.08,8.76s1.15,6.84,3.08,8.76c.21.21.31.48.31.75s-.1.54-.31.75-.48.31-.75.31-.54-.1-.75-.31c-4.12-4.09-13.51-4.09-17.63,0-.42.41-1.09.41-1.5,0h0ZM176.97,149.98c0-.98-.39-1.91-1.09-2.61-.72-.72-1.67-1.08-2.62-1.08s-1.9.36-2.62,1.08c-1.45,1.44-1.45,3.77,0,5.21,1.45,1.44,3.8,1.44,5.25,0,.7-.7,1.09-1.62,1.09-2.61h0ZM177.38,154.08c-1.14,1.13-2.63,1.7-4.13,1.7s-2.99-.57-4.13-1.7c-2.28-2.26-2.28-5.94,0-8.2,2.28-2.26,5.98-2.26,8.25,0,2.28,2.26,2.28,5.94,0,8.2h0ZM129.13,150c0-.98-.39-1.91-1.09-2.61-.7-.7-1.63-1.08-2.62-1.08s-1.92.38-2.62,1.08c-.7.7-1.09,1.62-1.09,2.61s.38,1.91,1.09,2.61c1.4,1.39,3.84,1.39,5.25,0,.7-.7,1.09-1.62,1.09-2.61h0ZM129.55,154.1c-1.14,1.13-2.63,1.7-4.13,1.7s-2.99-.57-4.13-1.7c-2.27-2.26-2.27-5.94,0-8.2,2.28-2.26,5.98-2.26,8.25,0s2.28,5.94,0,8.2h0ZM165.17,165.76c-4.24,4.22-9.89,6.54-15.89,6.54s-11.65-2.32-15.89-6.54c-2.91-2.89-4.51-6.35-5.34-8.74l-2.01.69c.9,2.6,2.65,6.36,5.85,9.54,4.65,4.62,10.82,7.16,17.4,7.16s12.75-2.54,17.39-7.16c2.68-2.67,4.82-6.05,6.02-9.54l-2.01-.68c-1.1,3.19-3.06,6.29-5.51,8.73h0ZM128.04,142.92l-2.01-.69c1.29-3.68,3.37-7.07,5.86-9.54,4.64-4.61,10.82-7.15,17.39-7.15s12.75,2.54,17.39,7.15c2.62,2.6,4.82,6.08,6.03,9.54l-2.01.69c-1.11-3.17-3.12-6.36-5.52-8.74-4.24-4.22-9.88-6.53-15.89-6.53s-11.64,2.32-15.89,6.53c-2.27,2.25-4.17,5.36-5.35,8.74h0Z"
                    fill="white"
                    fillRule="evenodd"
                  />
                </g>
              </g>

              <g>
                <rect
                  y="107.5"
                  width="85"
                  height="85"
                  fill="url(#dynamo-gradient)"
                />
                <g>
                  <path
                    d="M40.36,167c-.22,0-.44-.06-.63-.17-.5-.29-.72-.89-.55-1.44l6.87-21.57h-6.73c-.43,0-.83-.22-1.06-.59-.23-.37-.24-.83-.05-1.21l7.55-14.75c.21-.42.64-.68,1.1-.68h15.96c.41,0,.8.21,1.03.55s.27.78.11,1.16l-3.68,8.87h6.38c.5,0,.95.3,1.14.75.2.46.1.99-.25,1.34l-26.3,27.36c-.24.25-.57.38-.89.38ZM41.34,141.33h6.4c.4,0,.77.19,1,.51.23.32.3.73.18,1.11l-5.77,18.12,20.6-21.42h-5.32c-.41,0-.8-.21-1.03-.55s-.27-.78-.11-1.16l3.68-8.87h-13.34l-6.28,12.27Z"
                    fill="white"
                  />
                  <g>
                    <path
                      d="M37.21,153.76c-9.97,0-20.57-3.03-20.57-8.64,0-1.21.54-3.01,3.12-4.73l1.37,2.06c-.92.61-2.02,1.57-2.02,2.66,0,2.91,7.73,6.16,18.09,6.16,1.05,0,2.11-.03,3.13-.1l.16,2.47c-1.08.07-2.19.1-3.29.1Z"
                      fill="white"
                    />
                    <path
                      d="M52.06,160.52l-.97-2.28c2.51-1.07,4.04-2.39,4.19-3.61l2.46.3c-.27,2.19-2.23,4.12-5.68,5.59Z"
                      fill="white"
                    />
                    <path
                      d="M57.74,154.94l-2.46-.32c0-.07.01-.13.01-.2h2.48c0,.17,0,.34-.03.51Z"
                      fill="white"
                    />
                    <path
                      d="M37.21,163.07c-9.97,0-20.57-3.03-20.57-8.65h2.48c0,2.91,7.73,6.17,18.09,6.17h.48s.02,2.48.02,2.48h-.5Z"
                      fill="white"
                    />
                    <path
                      d="M26.24,157.51c-2.24-.57-4.14-1.29-5.67-2.16l1.22-2.16c1.31.75,3.06,1.41,5.06,1.91l-.61,2.4Z"
                      fill="white"
                    />
                    <rect
                      x="16.64"
                      y="145.12"
                      width="2.48"
                      height="9.31"
                      fill="white"
                    />
                  </g>
                  <g>
                    <path
                      d="M37.21,137.04c-9.97,0-20.57-3.03-20.57-8.64s10.6-8.65,20.57-8.65c5.67,0,10.91.89,14.77,2.52l-.96,2.29c-3.51-1.48-8.55-2.32-13.81-2.32-10.35,0-18.09,3.26-18.09,6.17s7.73,6.16,18.09,6.16c.28,0,.56,0,.83,0l.05,2.48c-.29,0-.59,0-.88,0Z"
                      fill="white"
                    />
                    <path
                      d="M34.86,146.3c-8.77-.4-18.22-3.26-18.22-8.59h2.48c0,2.61,6.37,5.69,15.85,6.11l-.11,2.48Z"
                      fill="white"
                    />
                    <path
                      d="M26.24,140.8c-2.24-.57-4.14-1.29-5.67-2.16l1.22-2.16c1.32.75,3.06,1.41,5.06,1.91l-.61,2.4Z"
                      fill="white"
                    />
                    <rect
                      x="16.64"
                      y="128.4"
                      width="2.48"
                      height="9.31"
                      fill="white"
                    />
                  </g>
                  <g>
                    <path
                      d="M37.21,170.48c-9.97,0-20.57-3.03-20.57-8.64,0-1.21.55-3.02,3.14-4.74l1.37,2.07c-.93.61-2.03,1.57-2.03,2.67,0,2.91,7.73,6.16,18.09,6.16s18.09-3.26,18.09-6.16c0-1.1-1.1-2.06-2.03-2.67l1.37-2.07c2.6,1.72,3.14,3.52,3.14,4.74,0,5.61-10.6,8.64-20.57,8.64Z"
                      fill="white"
                    />
                    <path
                      d="M37.21,179.79c-9.97,0-20.57-3.03-20.57-8.64h2.48c0,2.91,7.73,6.16,18.09,6.16s18.09-3.26,18.09-6.16h2.48c0,5.61-10.6,8.64-20.57,8.64Z"
                      fill="white"
                    />
                    <path
                      d="M26.24,174.23c-2.24-.57-4.14-1.29-5.67-2.16l1.22-2.16c1.31.75,3.06,1.41,5.06,1.91l-.61,2.4Z"
                      fill="white"
                    />
                    <rect
                      x="16.64"
                      y="161.83"
                      width="2.48"
                      height="9.31"
                      fill="white"
                    />
                    <rect
                      x="55.29"
                      y="161.83"
                      width="2.48"
                      height="9.31"
                      fill="white"
                    />
                  </g>
                </g>
              </g>

              <g>
                <rect
                  x="215"
                  y="107.5"
                  width="85"
                  height="85"
                  fill="url(#linear-gradient-4)"
                />
                <g>
                  <path
                    d="M255.88,138.42c-13.67,0-27.52-3.12-27.52-9.09s13.84-9.09,27.52-9.09,27.52,3.12,27.52,9.09-13.84,9.09-27.52,9.09ZM255.88,122.72c-15.51,0-25.04,3.85-25.04,6.61s9.53,6.61,25.04,6.61,25.04-3.85,25.04-6.61-9.53-6.61-25.04-6.61Z"
                    fill="white"
                  />
                  <path
                    d="M255.88,179.8c-7.7,0-15.07-1.44-20.22-3.96-.37-.18-.62-.53-.68-.94l-6.61-45.39,2.45-.36,6.51,44.73c4.79,2.18,11.5,3.43,18.54,3.43s13.75-1.25,18.54-3.43l6.51-44.73,2.45.36-6.6,45.39c-.06.41-.32.76-.68.94-5.15,2.52-12.52,3.96-20.22,3.96Z"
                    fill="white"
                  />
                  <g>
                    <circle cx="255.9" cy="144.34" r="1.64" fill="white" />
                    <path
                      d="M255.9,147.22c-1.59,0-2.88-1.29-2.88-2.88s1.29-2.88,2.88-2.88,2.88,1.29,2.88,2.88-1.29,2.88-2.88,2.88ZM255.9,143.94c-.22,0-.4.18-.4.4s.18.4.4.4.4-.18.4-.4-.18-.4-.4-.4Z"
                      fill="white"
                    />
                  </g>
                  <path
                    d="M283.79,155.66c-6.16,0-23.59-6.92-28.58-10.3l1.39-2.05c5.99,4.06,22.57,9.83,26.9,9.94-.59-.63-1.9-1.8-4.82-3.77l1.39-2.05c5.4,3.65,6.45,5.22,6.37,6.47-.05.65-.43,1.19-1.05,1.49-.37.18-.92.26-1.59.26Z"
                    fill="white"
                  />
                </g>
              </g>

              <g id="BottomMiddle">
                <path
                  d="M150,257.5v-107.5"
                  fill="none"
                  stroke="none"
                  ref={PATH_REFS.BottomMiddle[0]}
                />
                <path
                  d="M150,257.5c0-13.13,1.64-36.16,1.64-49.29,0-22.71-1.64-35.51-1.64-58.21"
                  fill="none"
                  stroke="none"
                  ref={PATH_REFS.BottomMiddle[1]}
                />
                <path
                  d="M150,257.5c0-13.13-1.64-36.16-1.64-49.29,0-22.71,1.64-35.51,1.64-58.21"
                  fill="none"
                  stroke="none"
                  ref={PATH_REFS.BottomMiddle[2]}
                />
                <path
                  d="M150,257.5c0-13.13,3.27-36.16,3.27-49.29,0-22.71-3.27-35.51-3.27-58.21"
                  fill="none"
                  stroke="none"
                  ref={PATH_REFS.BottomMiddle[3]}
                />
                <path
                  d="M150,257.5c0-13.13-3.27-36.16-3.27-49.29,0-22.71,3.27-35.51,3.27-58.21"
                  fill="none"
                  stroke="none"
                  ref={PATH_REFS.BottomMiddle[4]}
                />
              </g>

              <g id="BottomLeft">
                <g>
                  <path
                    d="M42.5,150c5.57,14.52,15.54,35.2,33.48,55.8,26.79,30.75,57.32,45.23,74.02,51.7"
                    fill="none"
                    stroke="none"
                    ref={PATH_REFS.BottomLeft[0]}
                  />
                  <path
                    d="M42.5,150c5.57,14.52,12.79,35.94,30.74,56.54,26.79,30.75,60.07,44.49,76.76,50.96"
                    fill="none"
                    stroke="none"
                    ref={PATH_REFS.BottomLeft[1]}
                  />
                  <path
                    d="M42.5,150c5.57,14.52,10.05,36.69,27.99,57.29,26.79,30.75,62.81,43.74,79.51,50.21"
                    fill="none"
                    stroke="none"
                    ref={PATH_REFS.BottomLeft[2]}
                  />
                  <path
                    d="M42.5,150c5.57,14.52,7.3,37.43,25.25,58.03,26.79,30.75,65.56,43,82.25,49.47"
                    fill="none"
                    stroke="none"
                    ref={PATH_REFS.BottomLeft[3]}
                  />
                  <path
                    d="M42.5,150c5.57,14.52,4.55,38.17,22.5,58.77,26.79,30.75,68.3,42.26,85,48.73"
                    fill="none"
                    stroke="none"
                    ref={PATH_REFS.BottomLeft[4]}
                  />
                </g>
              </g>

              <g id="BottomRight">
                <g>
                  <path
                    d="M257.5,150c-5.57,14.52-15.54,35.2-33.48,55.8-26.79,30.75-57.32,45.23-74.02,51.7"
                    fill="none"
                    stroke="none"
                    ref={PATH_REFS.BottomRight[0]}
                  />
                  <path
                    d="M257.5,150c-5.57,14.52-12.79,35.94-30.74,56.54-26.79,30.75-60.07,44.49-76.76,50.96"
                    fill="none"
                    stroke="none"
                    ref={PATH_REFS.BottomRight[1]}
                  />
                  <path
                    d="M257.5,150c-5.57,14.52-10.05,36.69-27.99,57.29-26.79,30.75-62.81,43.74-79.51,50.21"
                    fill="none"
                    stroke="none"
                    ref={PATH_REFS.BottomRight[2]}
                  />
                  <path
                    d="M257.5,150c-5.57,14.52-7.3,37.43-25.25,58.03-26.79,30.75-65.56,43-82.25,49.47"
                    fill="none"
                    stroke="none"
                    ref={PATH_REFS.BottomRight[3]}
                  />
                  <path
                    d="M257.5,150c-5.57,14.52-4.55,38.17-22.5,58.77-26.79,30.75-68.3,42.26-85,48.73"
                    fill="none"
                    stroke="none"
                    ref={PATH_REFS.BottomRight[4]}
                  />
                </g>
              </g>

              <g id="TopMiddle">
                <path
                  d="M150,150V42.5"
                  fill="none"
                  stroke="none"
                  ref={PATH_REFS.TopMiddle[0]}
                />
                <path
                  d="M150,150c0-13.13,1.64-36.16,1.64-49.29,0-22.71-1.64-35.51-1.64-58.21"
                  fill="none"
                  stroke="none"
                  ref={PATH_REFS.TopMiddle[1]}
                />
                <path
                  d="M150,150c0-13.13-1.64-36.16-1.64-49.29,0-22.71,1.64-35.51,1.64-58.21"
                  fill="none"
                  stroke="none"
                  ref={PATH_REFS.TopMiddle[2]}
                />
                <path
                  d="M150,150c0-13.13,3.27-36.16,3.27-49.29,0-22.71-3.27-35.51-3.27-58.21"
                  fill="none"
                  stroke="none"
                  ref={PATH_REFS.TopMiddle[3]}
                />
                <path
                  d="M150,150c0-13.13-3.27-36.16-3.27-49.29,0-22.71,3.27-35.51,3.27-58.21"
                  fill="none"
                  stroke="none"
                  ref={PATH_REFS.TopMiddle[4]}
                />
              </g>

              <g id="TopLeft">
                <g>
                  <path
                    d="M42.5,150c5.57-14.52,15.54-35.2,33.48-55.8,26.79-30.75,57.32-45.23,74.02-51.7"
                    fill="none"
                    stroke="none"
                    ref={PATH_REFS.TopLeft[0]}
                  />
                  <path
                    d="M42.5,150c5.57-14.52,12.79-35.94,30.74-56.54,26.79-30.75,60.07-44.49,76.76-50.96"
                    fill="none"
                    stroke="none"
                    ref={PATH_REFS.TopLeft[1]}
                  />
                  <path
                    d="M42.5,150c5.57-14.52,10.05-36.69,27.99-57.29,26.79-30.75,62.81-43.74,79.51-50.21"
                    fill="none"
                    stroke="none"
                    ref={PATH_REFS.TopLeft[2]}
                  />
                  <path
                    d="M42.5,150c5.57-14.52,7.3-37.43,25.25-58.03,26.79-30.75,65.56-43,82.25-49.47"
                    fill="none"
                    stroke="none"
                    ref={PATH_REFS.TopLeft[3]}
                  />
                  <path
                    d="M42.5,150c5.57-14.52,4.55-38.17,22.5-58.77,26.79-30.75,68.3-42.26,85-48.73"
                    fill="none"
                    stroke="none"
                    ref={PATH_REFS.TopLeft[4]}
                  />
                </g>
              </g>

              <g id="TopRight">
                <g>
                  <path
                    d="M257.5,150c-5.57-14.52-15.54-35.2-33.48-55.8-26.79-30.75-57.32-45.23-74.02-51.7"
                    fill="none"
                    stroke="none"
                    ref={PATH_REFS.TopRight[0]}
                  />
                  <path
                    d="M257.5,150c-5.57-14.52-12.79-35.94-30.74-56.54-26.79-30.75-60.07-44.49-76.76-50.96"
                    fill="none"
                    stroke="none"
                    ref={PATH_REFS.TopRight[1]}
                  />
                  <path
                    d="M257.5,150c-5.57-14.52-10.05-36.69-27.99-57.29-26.79-30.75-62.81-43.74-79.51-50.21"
                    fill="none"
                    stroke="none"
                    ref={PATH_REFS.TopRight[2]}
                  />
                  <path
                    d="M257.5,150c-5.57-14.52-7.3-37.43-25.25-58.03-26.79-30.75-65.56-43-82.25-49.47"
                    fill="none"
                    stroke="none"
                    ref={PATH_REFS.TopRight[3]}
                  />
                  <path
                    d="M257.5,150c-5.57-14.52-4.55-38.17-22.5-58.77-26.79-30.75-68.3-42.26-85-48.73"
                    fill="none"
                    stroke="none"
                    ref={PATH_REFS.TopRight[4]}
                  />
                </g>
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
      </div>
    </div>
  );
};

export default UploadDiagram;
