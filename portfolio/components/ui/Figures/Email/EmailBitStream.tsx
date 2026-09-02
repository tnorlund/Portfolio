import { animated, useSprings } from "@react-spring/web";
import React from "react";
import {
  fadeLUT,
  OPTIMIZED_SPRING_CONFIG,
  pointAtCached,
} from "../useDiagramOptimizations";

/**
 * Shared "1/0 glyphs travelling along hidden SVG paths" primitive used by
 * the two email diagrams. Same choreography model as AWSFlowDiagram /
 * UploadDiagram: a storyboard of phases, each phase launching a trail of
 * bits along one or more paths in a direction.
 */

export type PathRef = React.RefObject<SVGPathElement | null>;

export const BIT_COUNT = 15;
export const TILT = 30; // ±30°
export const PHASE_LEN = 500; // default travel time per leg (ms)
export const STAGGER = 50; // pause between legs (ms)
export const CYCLE_PAUSE = 400; // pause between storyboard loops (ms)
export const LAUNCH_STEP = 50; // per-glyph trail spacing (ms)

export type Phase<Name extends string> = {
  paths: Name[];
  dir: 1 | -1;
  duration?: number;
  launch?: number;
  /** glyphs in this leg's trail (default BIT_COUNT) */
  count?: number;
  /** glyph characters for this leg (default alternating 1/0) */
  chars?: string[];
};

export function phaseLength<Name extends string>(p: Phase<Name>): number {
  return (
    (p.duration ?? PHASE_LEN) +
    ((p.count ?? BIT_COUNT) - 1) * (p.launch ?? LAUNCH_STEP)
  );
}

export function delayFor<Name extends string>(
  timeline: Phase<Name>[],
  idx: number,
): number {
  return timeline
    .slice(0, idx)
    .reduce((acc, p) => acc + phaseLength(p) + STAGGER, 0);
}

export function totalCycleMs<Name extends string>(timeline: Phase<Name>[]): number {
  return (
    timeline.reduce((acc, p) => acc + phaseLength(p) + STAGGER, 0) + CYCLE_PAUSE
  );
}

/** Five gently-jittered copies of a straight/curved leg between two points. */
export function makeRefs(): PathRef[] {
  return Array.from({ length: 5 }, () => React.createRef<SVGPathElement>());
}

/**
 * Renders five near-parallel invisible paths from (x1,y1) to (x2,y2) so the
 * bits fan out a little instead of riding a single rail.
 */
export function FanPaths({
  refs,
  x1,
  y1,
  x2,
  y2,
}: {
  refs: PathRef[];
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}) {
  const dx = x2 - x1;
  const dy = y2 - y1;
  // Perpendicular unit vector for the fan offset.
  const len = Math.hypot(dx, dy) || 1;
  const px = -dy / len;
  const py = dx / len;
  const offsets = [0, 1.6, -1.6, 3.2, -3.2];
  return (
    <g>
      {refs.map((ref, i) => {
        const o = offsets[i];
        const mx = x1 + dx / 2 + px * o * 2;
        const my = y1 + dy / 2 + py * o * 2;
        return (
          <path
            key={i}
            d={`M${x1},${y1} Q${mx},${my} ${x2},${y2}`}
            fill="none"
            stroke="none"
            ref={ref}
          />
        );
      })}
    </g>
  );
}

type Bit = { char: string; rot: number; pathIdx: number };

export function BitStream({
  pathRefs,
  dir,
  duration = PHASE_LEN,
  launch = LAUNCH_STEP,
  initialDelay = 0,
  chars,
  pause,
  count = BIT_COUNT,
}: {
  pathRefs: PathRef[];
  dir: 1 | -1;
  duration?: number;
  launch?: number;
  initialDelay?: number;
  chars?: string[];
  pause?: boolean;
  /** glyphs in the trail; defaults to BIT_COUNT */
  count?: number;
}) {
  const bits = React.useMemo<Bit[]>(
    () =>
      Array.from({ length: count }, (_, idx) => ({
        char: chars?.[idx % chars.length] ?? (idx % 2 === 0 ? "1" : "0"),
        rot: ((idx * 7.3) % TILT) - TILT / 2,
        pathIdx: idx % pathRefs.length,
      })),
    [pathRefs.length, chars, count],
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
            rx="2"
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

/** Restart the storyboard after each full cycle while visible. */
export function useCycle<Name extends string>(
  timeline: Phase<Name>[],
  shouldAnimate: boolean,
): number {
  const [cycle, setCycle] = React.useState(0);
  React.useEffect(() => {
    if (!shouldAnimate) return;
    const id = setTimeout(() => setCycle((c) => c + 1), totalCycleMs(timeline));
    return () => clearTimeout(id);
  }, [timeline, cycle, shouldAnimate]);
  return cycle;
}

export const LABEL_TEXT_PROPS = {
  fontFamily:
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
  fontSize: "11",
  fill: "var(--text-color)",
  fontWeight: 600,
  textAnchor: "middle",
} as const;

/* ─── Shared AWS-style glyphs ─────────────────────────────────────── */

/** The S3 bucket icon from UploadDiagram, re-centred on (0,0) in an 85×85 box. */
export function S3Icon({ x, y, gradientId }: { x: number; y: number; gradientId: string }) {
  return (
    <g transform={`translate(${x - 42.5},${y - 42.5})`}>
      <rect width="85" height="85" fill={`url(#${gradientId})`} rx="6" />
      <g transform="translate(-213.38,-107.5)">
        <path
          d="M255.88,138.42c-13.67,0-27.52-3.12-27.52-9.09s13.84-9.09,27.52-9.09,27.52,3.12,27.52,9.09-13.84,9.09-27.52,9.09ZM255.88,122.72c-15.51,0-25.04,3.85-25.04,6.61s9.53,6.61,25.04,6.61,25.04-3.85,25.04-6.61-9.53-6.61-25.04-6.61Z"
          fill="white"
        />
        <path
          d="M255.88,179.8c-7.7,0-15.07-1.44-20.22-3.96-.37-.18-.62-.53-.68-.94l-6.61-45.39,2.45-.36,6.51,44.73c4.79,2.18,11.5,3.43,18.54,3.43s13.75-1.25,18.54-3.43l6.51-44.73,2.45.36-6.6,45.39c-.06.41-.32.76-.68.94-5.15,2.52-12.52,3.96-20.22,3.96Z"
          fill="white"
        />
        <circle cx="255.9" cy="144.34" r="1.64" fill="white" />
        <path
          d="M255.9,147.22c-1.59,0-2.88-1.29-2.88-2.88s1.29-2.88,2.88-2.88,2.88,1.29,2.88,2.88-1.29,2.88-2.88,2.88ZM255.9,143.94c-.22,0-.4.18-.4.4s.18.4.4.4.4-.18.4-.4-.18-.4-.4-.4Z"
          fill="white"
        />
        <path
          d="M283.79,155.66c-6.16,0-23.59-6.92-28.58-10.3l1.39-2.05c5.99,4.06,22.57,9.83,26.9,9.94-.59-.63-1.9-1.8-4.82-3.77l1.39-2.05c5.4,3.65,6.45,5.22,6.37,6.47-.05.65-.43,1.19-1.05,1.49-.37.18-.92.26-1.59.26Z"
          fill="white"
        />
      </g>
    </g>
  );
}

/** The Lambda icon from UploadDiagram, re-centred on (0,0) in an 85×85 box. */
export function LambdaIcon({
  x,
  y,
  gradientId,
}: {
  x: number;
  y: number;
  gradientId: string;
}) {
  return (
    <g transform={`translate(${x - 42.5},${y - 42.5})`}>
      <rect width="85" height="85" fill={`url(#${gradientId})`} rx="6" />
      <g transform="translate(-107.5,-215)">
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
  );
}

/** A plain envelope, 56 wide, centred on (0,0). */
export function EnvelopeIcon({
  x,
  y,
  fill = "var(--text-color)",
  stroke = "var(--background-color)",
  scale = 1,
}: {
  x: number;
  y: number;
  fill?: string;
  stroke?: string;
  scale?: number;
}) {
  return (
    <g transform={`translate(${x},${y}) scale(${scale})`}>
      <rect x="-28" y="-19" width="56" height="38" rx="4" fill={fill} />
      <path
        d="M-28,-15 L0,4 L28,-15"
        fill="none"
        stroke={stroke}
        strokeWidth="3"
        strokeLinejoin="round"
      />
    </g>
  );
}

/** The MacBook glyph from AWSFlowDiagram, centred on (0,0), ~110 wide. */
export function LaptopIcon({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x - 150},${y - 40})`}>
      <path
        d="M204.49,71.69h0V5.74c0-3.17-2.57-5.74-5.74-5.74h-97.5c-3.17,0-5.74,2.57-5.74,5.74v65.96h-11.47v3.48c0,2.13,1.72,3.69,3.85,3.69h6.81l.62,1.43h7.37l.62-1.43h92.57l.62,1.43h7.37l.61-1.43h7.62c2.13,0,3.85-1.57,3.85-3.69v-3.48h-11.47ZM99.82,71.69V5.74c0-.79.64-1.43,1.43-1.43h41.58v.17c0,1.49,1.21,2.7,2.7,2.7h8.95c1.49,0,2.7-1.21,2.7-2.7v-.17h41.58c.79,0,1.43.64,1.43,1.43v65.96h-100.37Z"
        fill="var(--text-color)"
      />
    </g>
  );
}

/** A phone with a chat bubble — "the MCP client, wherever it is". */
export function ClientIcon({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x},${y})`}>
      <rect
        x="-18"
        y="-36"
        width="36"
        height="72"
        rx="7"
        fill="none"
        stroke="var(--text-color)"
        strokeWidth="3.5"
      />
      <rect x="-6" y="27" width="12" height="3" rx="1.5" fill="var(--text-color)" />
      <path
        d="M-11,-16 h22 a4,4 0 0 1 4,4 v10 a4,4 0 0 1 -4,4 h-10 l-6,5 v-5 h-6 a4,4 0 0 1 -4,-4 v-10 a4,4 0 0 1 4,-4 z"
        fill="var(--text-color)"
      />
    </g>
  );
}

/** Amazon SES, from the AWS Architecture Icons set (Arch_Amazon-Simple-Email-Service_64). */
export function SesIcon({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x - 42.5},${y - 42.5})`}>
      <rect width="85" height="85" fill="url(#email-ses-gradient)" rx="6" />
      <g transform="translate(2.5,2.5)">
        <path
          fill="white"
          fillRule="evenodd"
          d="M57,60.999875 C57,59.373846 55.626,57.9998214 54,57.9998214 C52.374,57.9998214 51,59.373846 51,60.999875 C51,62.625904 52.374,63.9999286 54,63.9999286 C55.626,63.9999286 57,62.625904 57,60.999875 L57,60.999875 Z M40,59.9998571 C38.374,59.9998571 37,61.3738817 37,62.9999107 C37,64.6259397 38.374,65.9999643 40,65.9999643 C41.626,65.9999643 43,64.6259397 43,62.9999107 C43,61.3738817 41.626,59.9998571 40,59.9998571 L40,59.9998571 Z M26,57.9998214 C24.374,57.9998214 23,59.373846 23,60.999875 C23,62.625904 24.374,63.9999286 26,63.9999286 C27.626,63.9999286 29,62.625904 29,60.999875 C29,59.373846 27.626,57.9998214 26,57.9998214 L26,57.9998214 Z M28.605,42.9995536 L51.395,42.9995536 L43.739,36.1104305 L40.649,38.7584778 C40.463,38.9194807 40.23,38.9994821 39.999,38.9994821 C39.768,38.9994821 39.535,38.9194807 39.349,38.7584778 L36.26,36.1104305 L28.605,42.9995536 Z M27,28.1732888 L27,41.7545313 L34.729,34.7984071 L27,28.1732888 Z M51.297,26.9992678 L28.703,26.9992678 L39.999,36.6824408 L51.297,26.9992678 Z M53,41.7545313 L53,28.1732888 L45.271,34.7974071 L53,41.7545313 Z M59,60.999875 C59,63.7099234 56.71,65.9999643 54,65.9999643 C51.29,65.9999643 49,63.7099234 49,60.999875 C49,58.6308327 50.75,56.5837961 53,56.1057876 L53,52.9997321 L41,52.9997321 L41,58.1058233 C43.25,58.5838319 45,60.6308684 45,62.9999107 C45,65.7099591 42.71,68 40,68 C37.29,68 35,65.7099591 35,62.9999107 C35,60.6308684 36.75,58.5838319 39,58.1058233 L39,52.9997321 L27,52.9997321 L27,56.1057876 C29.25,56.5837961 31,58.6308327 31,60.999875 C31,63.7099234 28.71,65.9999643 26,65.9999643 C23.29,65.9999643 21,63.7099234 21,60.999875 C21,58.6308327 22.75,56.5837961 25,56.1057876 L25,51.9997143 C25,51.4477044 25.447,50.9996964 26,50.9996964 L39,50.9996964 L39,44.9995893 L26,44.9995893 C25.447,44.9995893 25,44.5515813 25,43.9995714 L25,25.99925 C25,25.4472401 25.447,24.9992321 26,24.9992321 L54,24.9992321 C54.553,24.9992321 55,25.4472401 55,25.99925 L55,43.9995714 C55,44.5515813 54.553,44.9995893 54,44.9995893 L41,44.9995893 L41,50.9996964 L54,50.9996964 C54.553,50.9996964 55,51.4477044 55,51.9997143 L55,56.1057876 C57.25,56.5837961 59,58.6308327 59,60.999875 L59,60.999875 Z M68,39.9995 C68,45.9066055 66.177,51.5597064 62.727,56.3447919 L61.104,55.174771 C64.307,50.7316916 66,45.4845979 66,39.9995 C66,25.664244 54.337,14.0000357 40.001,14.0000357 C25.664,14.0000357 14,25.664244 14,39.9995 C14,45.4845979 15.693,50.7316916 18.896,55.174771 L17.273,56.3447919 C13.823,51.5597064 12,45.9066055 12,39.9995 C12,24.5612243 24.561,12 39.999,12 C55.438,12 68,24.5612243 68,39.9995 L68,39.9995 Z"
        />
      </g>
    </g>
  );
}

/** The Grok Bot app icon (public/grok-bot-icon.svg), in the same 85×85 slot. */
export function GrokIcon({ x, y }: { x: number; y: number }) {
  return (
    <image
      href="/grok-bot-icon.svg"
      x={x - 46}
      y={y - 46}
      width="92"
      height="92"
    />
  );
}

/** The Claude app icon (public/claude-icon.svg), in the same 85×85 slot. */
export function ClaudeIcon({ x, y }: { x: number; y: number }) {
  return (
    <image href="/claude-icon.svg" x={x - 46} y={y - 46} width="92" height="92" />
  );
}

export function EmailGradients() {
  return (
    <defs>
      <linearGradient id="email-s3-gradient" x1="0" y1="1" x2="1" y2="0">
        <stop offset="0" stopColor="#1f6835" />
        <stop offset="1" stopColor="#6bad44" />
      </linearGradient>
      <linearGradient id="email-lambda-gradient" x1="0" y1="1" x2="1" y2="0">
        <stop offset="0" stopColor="#c85428" />
        <stop offset="1" stopColor="#f8981d" />
      </linearGradient>
      <linearGradient id="email-ses-gradient" x1="0" y1="1" x2="1" y2="0">
        <stop offset="0" stopColor="#bd0816" />
        <stop offset="1" stopColor="#ff5252" />
      </linearGradient>
    </defs>
  );
}
