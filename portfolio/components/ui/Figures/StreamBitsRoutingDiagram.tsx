import { animated, useSprings } from "@react-spring/web";
import React from "react";

type PathRef = React.RefObject<SVGPathElement | null>;
type RouteName =
    | "TopRobotToStream"
    | "MiddleRobotToStream"
    | "BottomRobotToStream"
    | "StreamToDynamo"
    | "StreamToLambda";

type Phase = {
    routes: RouteName[];
    dir: 1 | -1;
    durationMs?: number;
    label?: string;
};

const BIT_COUNT = 14;
const DEFAULT_PHASE_MS = 900;
const LAUNCH_STEP_MS = 55;
const STAGGER_MS = 120;
const CYCLE_PAUSE_MS = 300;
const TILT_DEG = 26;

const FADE = (p: number) => 1 - Math.abs((p % 100) - 50) / 50; // 0→1→0

const clampMs = (n: number) => Math.max(1, Math.round(n));

function mulberry32(seed: number) {
    let t = seed >>> 0;
    return () => {
        t += 0x6d2b79f5;
        let r = Math.imul(t ^ (t >>> 15), 1 | t);
        r ^= r + Math.imul(r ^ (r >>> 7), 61 | r);
        return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
    };
}

function shuffle<T>(arr: T[], rand: () => number) {
    const a = [...arr];
    for (let i = a.length - 1; i > 0; i--) {
        const j = Math.floor(rand() * (i + 1));
        [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
}

const LABEL_TEXT_PROPS = {
    fontFamily:
        "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
    fontSize: "10",
    fill: "var(--text-color)",
    fontWeight: 600,
} as const;

const makeRefs = () =>
    Array.from({ length: 5 }, () => React.createRef<SVGPathElement>());

function pointAt(ref: PathRef, pct: number) {
    const el = ref.current;
    if (!el) return { x: 0, y: 0 };
    const len = el.getTotalLength();
    return el.getPointAtLength(((pct % 100) / 100) * len);
}

function phaseLengthMs(durationMs: number, launchStepMs: number) {
    return durationMs + (BIT_COUNT - 1) * launchStepMs;
}

function BitStream({
    pathRefs,
    dir,
    durationMs,
    initialDelayMs,
    launchStepMs,
    cycleKey,
}: {
    pathRefs: PathRef[];
    dir: 1 | -1;
    durationMs: number;
    initialDelayMs: number;
    launchStepMs: number;
    cycleKey: number;
}) {
    type Bit = { char: "0" | "1"; rot: number; pathIdx: number };

    const bits = React.useMemo<Bit[]>(
        () =>
            Array.from({ length: BIT_COUNT }, (_, idx) => ({
                char: (idx % 2 === 0 ? "1" : "0") as "0" | "1",
                rot: ((idx * 7.3) % TILT_DEG) - TILT_DEG / 2,
                pathIdx: idx % pathRefs.length,
            })),
        [pathRefs.length]
    );

    const [springs] = useSprings(
        bits.length,
        (i) => ({
            from: { pct: dir === -1 ? 100 : 0 },
            to: { pct: dir === -1 ? 0 : 100 },
            config: { duration: durationMs, precision: 1, easing: (t: number) => t },
            delay: initialDelayMs + i * launchStepMs,
        }),
        [cycleKey, dir, durationMs, initialDelayMs, launchStepMs]
    );

    return (
        <>
            {springs.map((spring, i) => (
                <animated.g
                    key={i}
                    transform={spring.pct.to((p) => {
                        const { x, y } = pointAt(pathRefs[bits[i].pathIdx], p);
                        return `translate(${x},${y}) rotate(${bits[i].rot})`;
                    })}
                    opacity={spring.pct.to(FADE)}
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
                        fontFamily="monospace"
                        fontSize="12"
                    >
                        {bits[i].char}
                    </text>
                </animated.g>
            ))}
        </>
    );
}

/**
 * Lightweight “bits routing” figure driven by the exact Illustrator path data from Diagram-41.
 *
 * Route example requested: “SQS routing between the mac and Lambda”
 * - We treat left-middle node as “Mac”
 * - Center node as “SQS/Stream”
 * - Bottom-right as “Lambda”
 * and animate bits along: MiddleRobotToStream → StreamToLambda
 */
const StreamBitsRoutingDiagram: React.FC<{
    width?: number;
    height?: number;
    /** If true, includes extra routes beyond the Mac ⇄ SQS ⇄ Lambda example. */
    showExtraRoutes?: boolean;
    /** If true, renders small labels under nodes (off by default; SVG already has logos). */
    showLabels?: boolean;
    /**
     * If true, renders a tiny overlay listing the phase order.
     * Useful for reviewing animation order while tuning.
     */
    debugShowPhaseOrder?: boolean;
    /** Multiplies animation speed. >1 = faster. */
    timeScale?: number;
    /** If true, randomizes the order of robot→stream phases every cycle (fan-out always follows). */
    randomizeRobotOrder?: boolean;
}> = ({
    width = 300,
    height = 300,
    showExtraRoutes = false,
    showLabels = false,
    debugShowPhaseOrder = false,
    timeScale = 1.35,
    randomizeRobotOrder = true,
}) => {
        const PATH_REFS = React.useMemo(
            () => ({
                TopRobotToStream: makeRefs(),
                MiddleRobotToStream: makeRefs(),
                BottomRobotToStream: makeRefs(),
                StreamToDynamo: makeRefs(),
                StreamToLambda: makeRefs(),
            }),
            []
        );

        const [cycleKey, setCycleKey] = React.useState(0);

        const launchStepMs = clampMs(LAUNCH_STEP_MS / timeScale);
        const staggerMs = clampMs(STAGGER_MS / timeScale);
        const cyclePauseMs = clampMs(CYCLE_PAUSE_MS / timeScale);
        const defaultPhaseMs = clampMs(DEFAULT_PHASE_MS / timeScale);

        const phases = React.useMemo<Phase[]>(() => {
            const robots: Phase[] = [
                { routes: ["TopRobotToStream"], dir: -1, label: "Top Robot → Stream" },
                { routes: ["MiddleRobotToStream"], dir: 1, label: "Middle Robot → Stream" },
                { routes: ["BottomRobotToStream"], dir: -1, label: "Bottom Robot → Stream" },
            ];

            const orderedRobots = randomizeRobotOrder
                ? shuffle(robots, mulberry32(cycleKey + 1))
                : robots;

            const out: Phase[] = [];
            for (const r of orderedRobots) {
                out.push({ ...r, durationMs: defaultPhaseMs });
                out.push({
                    routes: ["StreamToLambda", "StreamToDynamo"],
                    dir: 1,
                    durationMs: defaultPhaseMs,
                    label: "Stream → Lambda + DynamoDB",
                });
            }

            if (showExtraRoutes) {
                out.push({
                    routes: ["StreamToLambda"],
                    dir: -1,
                    durationMs: defaultPhaseMs,
                    label: "Lambda → Stream",
                });
            }

            return out;
        }, [cycleKey, defaultPhaseMs, randomizeRobotOrder, showExtraRoutes]);

        const delayForPhase = React.useCallback(
            (phaseIdx: number) =>
                phases.slice(0, phaseIdx).reduce((acc, p) => {
                    const d = p.durationMs ?? defaultPhaseMs;
                    return acc + phaseLengthMs(d, launchStepMs) + staggerMs;
                }, 0),
            [defaultPhaseMs, launchStepMs, phases, staggerMs]
        );

        React.useEffect(() => {
            const totalMs =
                phases.reduce((acc, p) => {
                    const d = p.durationMs ?? defaultPhaseMs;
                    return acc + phaseLengthMs(d, launchStepMs) + staggerMs;
                }, 0) + cyclePauseMs;
            const id = setTimeout(() => setCycleKey((c) => c + 1), totalMs);
            return () => clearTimeout(id);
        }, [cycleKey, cyclePauseMs, defaultPhaseMs, launchStepMs, phases, staggerMs]);

        return (
            <div style={{ display: "flex", justifyContent: "center" }}>
                <svg
                    width={width}
                    height={height}
                    viewBox="0 0 300 300"
                    // Keep the Illustrator-exported scale (no responsive scaling).
                    style={{ overflow: "visible", display: "block" }}
                >
                    <defs>
                        <style>{`
            .route {
              fill: none;
              stroke: var(--text-color);
              stroke-opacity: 0.18;
              stroke-width: 1.25;
              stroke-linecap: round;
            }

            /* Diagram-41.svg logo styling (kept close to export) */
            .cls-1 { fill: none; stroke: var(--text-color); stroke-miterlimit: 10; stroke-opacity: 0.18; }
            .cls-2 { fill: url(#linear-gradient-2); }
            .cls-3 { fill: url(#linear-gradient); }
            .cls-4 { fill: var(--text-color); fill-rule: evenodd; }
            .cls-5 { fill: #5c77ba; fill-rule: evenodd; }
            .cls-6 { fill: #fff; }
          `}</style>

                        {/* Gradients from Diagram-41.svg */}
                        <linearGradient
                            id="linear-gradient"
                            x1="214.75"
                            y1="192.75"
                            x2="274.85"
                            y2="132.65"
                            gradientUnits="userSpaceOnUse"
                        >
                            <stop offset="0" stopColor="#3b3f99" />
                            <stop offset="1" stopColor="#5c76ba" />
                        </linearGradient>
                        <linearGradient
                            id="linear-gradient-2"
                            x1="214.75"
                            y1="300.25"
                            x2="274.85"
                            y2="240.15"
                            gradientUnits="userSpaceOnUse"
                        >
                            <stop offset="0" stopColor="#c85428" />
                            <stop offset="1" stopColor="#f8981d" />
                        </linearGradient>
                    </defs>

                    {/* Routes are intentionally hidden; animated bits still follow invisible path refs below. */}

                    {/* Logos from Diagram-41.svg */}
                    <g aria-hidden="true">
                        {/* Left-side robots */}
                        <g id="TopRobot">
                            <path
                                className="cls-4"
                                d="M44.21,12.97v8.41h23.18c3.76,0,6.83,3.07,6.83,6.83v8.63h9.28c.82,0,1.5.67,1.5,1.5v17.49c0,.82-.67,1.5-1.5,1.5h-9.28v8.63c0,3.76-3.07,6.83-6.83,6.83H16.89c-3.76,0-6.83-3.07-6.83-6.83v-8.63H1.5c-.83,0-1.5-.67-1.5-1.49v-17.49c0-.82.67-1.5,1.5-1.5h8.56v-8.62c0-3.76,3.07-6.83,6.83-6.83h23.18v-8.42c-2.66-.87-4.58-3.37-4.58-6.32,0-3.67,2.97-6.65,6.65-6.65s6.65,2.97,6.65,6.65c0,2.95-1.92,5.45-4.58,6.32h0ZM28.38,55.16h28.23c1.38,0,2.5,1.13,2.5,2.5v1.18c0,1.38-1.13,2.5-2.5,2.5h-28.23c-1.38,0-2.5-1.13-2.5-2.5v-1.18c0-1.38,1.13-2.5,2.5-2.5h0ZM54.44,32.92c3.71,0,6.73,3.01,6.73,6.73s-3.01,6.72-6.73,6.72-6.72-3.01-6.72-6.72c0-3.72,3.01-6.73,6.72-6.73h0ZM30.56,32.92c3.71,0,6.72,3.01,6.72,6.73s-3.01,6.72-6.72,6.72-6.72-3.01-6.72-6.72,3.01-6.73,6.72-6.73h0Z"
                            />
                        </g>
                        <g id="MiddleRobot">
                            <path
                                className="cls-4"
                                d="M44.21,126.58v8.41h23.18c3.76,0,6.83,3.07,6.83,6.83v8.63h9.28c.82,0,1.5.67,1.5,1.5v17.49c0,.82-.67,1.5-1.5,1.5h-9.28v8.63c0,3.76-3.07,6.83-6.83,6.83H16.89c-3.76,0-6.83-3.07-6.83-6.83v-8.63H1.5c-.83,0-1.5-.67-1.5-1.49v-17.49c0-.82.67-1.5,1.5-1.5h8.56v-8.62c0-3.76,3.07-6.83,6.83-6.83h23.18v-8.42c-2.66-.87-4.58-3.37-4.58-6.32,0-3.67,2.97-6.65,6.65-6.65s6.65,2.97,6.65,6.65c0,2.95-1.92,5.45-4.58,6.32h0ZM28.38,168.77h28.23c1.38,0,2.5,1.13,2.5,2.5v1.18c0,1.38-1.13,2.5-2.5,2.5h-28.23c-1.38,0-2.5-1.13-2.5-2.5v-1.18c0-1.38,1.13-2.5,2.5-2.5h0ZM54.44,146.53c3.71,0,6.73,3.01,6.73,6.73s-3.01,6.72-6.73,6.72-6.72-3.01-6.72-6.72c0-3.72,3.01-6.73,6.72-6.73h0ZM30.56,146.53c3.71,0,6.72,3.01,6.72,6.73s-3.01,6.72-6.72,6.72-6.72-3.01-6.72-6.72,3.01-6.73,6.72-6.73h0Z"
                            />
                        </g>
                        <g id="BottomRobot">
                            <path
                                className="cls-4"
                                d="M44.21,240.19v8.41h23.18c3.76,0,6.83,3.07,6.83,6.83v8.63h9.28c.82,0,1.5.67,1.5,1.5v17.49c0,.82-.67,1.5-1.5,1.5h-9.28v8.63c0,3.76-3.07,6.83-6.83,6.83H16.89c-3.76,0-6.83-3.07-6.83-6.83v-8.63H1.5c-.83,0-1.5-.67-1.5-1.49v-17.49c0-.82.67-1.5,1.5-1.5h8.56v-8.62c0-3.76,3.07-6.83,6.83-6.83h23.18v-8.42c-2.66-.87-4.58-3.37-4.58-6.32,0-3.67,2.97-6.65,6.65-6.65s6.65,2.97,6.65,6.65c0,2.95-1.92,5.45-4.58,6.32h0ZM28.38,282.38h28.23c1.38,0,2.5,1.13,2.5,2.5v1.18c0,1.38-1.13,2.5-2.5,2.5h-28.23c-1.38,0-2.5-1.13-2.5-2.5v-1.18c0-1.38,1.13-2.5,2.5-2.5h0ZM54.44,260.14c3.71,0,6.73,3.01,6.73,6.73s-3.01,6.72-6.73,6.72-6.72-3.01-6.72-6.72c0-3.72,3.01-6.73,6.72-6.73h0ZM30.56,260.14c3.71,0,6.72,3.01,6.72,6.73s-3.01,6.72-6.72,6.72-6.72-3.01-6.72-6.72,3.01-6.73,6.72-6.73h0Z"
                            />
                        </g>

                        {/* Center: DynamoDB Stream icon (explicit paths from updated /Users/tnorlund/Diagram-41.svg) */}
                        <g id="DynamoStream" aria-hidden="true">
                            <g>
                                <rect
                                    x="109.44"
                                    y="128.47"
                                    width="19.48"
                                    height="42.72"
                                    fill="none"
                                    stroke="#5c77ba"
                                    strokeWidth="4"
                                    strokeLinejoin="round"
                                    strokeMiterlimit="10"
                                />
                                <rect
                                    x="134.47"
                                    y="128.47"
                                    width="19.48"
                                    height="42.72"
                                    fill="none"
                                    stroke="#5c77ba"
                                    strokeWidth="4"
                                    strokeLinejoin="round"
                                    strokeMiterlimit="10"
                                />
                                <rect
                                    x="166.05"
                                    y="130.57"
                                    width="19.48"
                                    height="42.72"
                                    transform="translate(45.31 -40.32) rotate(15)"
                                    fill="none"
                                    stroke="#5c77ba"
                                    strokeWidth="4"
                                    strokeLinejoin="round"
                                    strokeMiterlimit="10"
                                />
                                <line
                                    x1="107.5"
                                    y1="109.43"
                                    x2="192.5"
                                    y2="109.43"
                                    fill="none"
                                    stroke="#5c77ba"
                                    strokeWidth="4"
                                    strokeMiterlimit="10"
                                />
                                <line
                                    x1="107.5"
                                    y1="190.57"
                                    x2="192.5"
                                    y2="190.57"
                                    fill="none"
                                    stroke="#5c77ba"
                                    strokeWidth="4"
                                    strokeMiterlimit="10"
                                />
                            </g>
                        </g>

                        {/* Top-right tile */}
                        <g>
                            <rect className="cls-3" x="215" y="107.5" width="85" height="85" />
                            <g>
                                <path
                                    className="cls-6"
                                    d="M255.36,167c-.22,0-.44-.06-.63-.17-.5-.29-.72-.89-.55-1.44l6.87-21.57h-6.73c-.43,0-.83-.22-1.06-.59-.23-.37-.24-.83-.05-1.21l7.55-14.75c.21-.42.64-.68,1.1-.68h15.96c.41,0,.8.21,1.03.55s.27.78.11,1.16l-3.68,8.87h6.38c.5,0,.95.3,1.14.75.2.46.1.99-.25,1.34l-26.3,27.36c-.24.25-.57.38-.89.38ZM256.34,141.33h6.4c.4,0,.77.19,1,.51.23.32.3.73.18,1.11l-5.77,18.12,20.6-21.42h-5.32c-.41,0-.8-.21-1.03-.55s-.27-.78-.11-1.16l3.68-8.87h-13.34l-6.28,12.27Z"
                                />
                                <g>
                                    <path
                                        className="cls-6"
                                        d="M252.21,153.76c-9.97,0-20.57-3.03-20.57-8.64,0-1.21.54-3.01,3.12-4.73l1.37,2.06c-.92.61-2.02,1.57-2.02,2.66,0,2.91,7.73,6.16,18.09,6.16,1.05,0,2.11-.03,3.13-.1l.16,2.47c-1.08.07-2.19.1-3.29.1Z"
                                    />
                                    <path
                                        className="cls-6"
                                        d="M267.06,160.52l-.97-2.28c2.51-1.07,4.04-2.39,4.19-3.61l2.46.3c-.27,2.19-2.23,4.12-5.68,5.59Z"
                                    />
                                    <path
                                        className="cls-6"
                                        d="M272.74,154.94l-2.46-.32c0-.07.01-.13.01-.2h2.48c0,.17,0,.34-.03.51Z"
                                    />
                                    <path
                                        className="cls-6"
                                        d="M252.21,163.07c-9.97,0-20.57-3.03-20.57-8.65h2.48c0,2.91,7.73,6.17,18.09,6.17h.48s.02,2.48.02,2.48h-.5Z"
                                    />
                                    <path
                                        className="cls-6"
                                        d="M241.24,157.51c-2.24-.57-4.14-1.29-5.67-2.16l1.22-2.16c1.31.75,3.06,1.41,5.06,1.91l-.61,2.4Z"
                                    />
                                    <rect
                                        className="cls-6"
                                        x="231.64"
                                        y="145.12"
                                        width="2.48"
                                        height="9.31"
                                    />
                                </g>
                                <g>
                                    <path
                                        className="cls-6"
                                        d="M252.21,137.04c-9.97,0-20.57-3.03-20.57-8.64s10.6-8.65,20.57-8.65c5.67,0,10.91.89,14.77,2.52l-.96,2.29c-3.51-1.48-8.55-2.32-13.81-2.32-10.35,0-18.09,3.26-18.09,6.17s7.73,6.16,18.09,6.16c.28,0,.56,0,.83,0l.05,2.48c-.29,0-.59,0-.88,0Z"
                                    />
                                    <path
                                        className="cls-6"
                                        d="M249.86,146.3c-8.77-.4-18.22-3.26-18.22-8.59h2.48c0,2.61,6.37,5.69,15.85,6.11l-.11,2.48Z"
                                    />
                                    <path
                                        className="cls-6"
                                        d="M241.24,140.8c-2.24-.57-4.14-1.29-5.67-2.16l1.22-2.16c1.32.75,3.06,1.41,5.06,1.91l-.61,2.4Z"
                                    />
                                    <rect
                                        className="cls-6"
                                        x="231.64"
                                        y="128.4"
                                        width="2.48"
                                        height="9.31"
                                    />
                                </g>
                                <g>
                                    <path
                                        className="cls-6"
                                        d="M252.21,170.48c-9.97,0-20.57-3.03-20.57-8.64,0-1.21.55-3.02,3.14-4.74l1.37,2.07c-.93.61-2.03,1.57-2.03,2.67,0,2.91,7.73,6.16,18.09,6.16s18.09-3.26,18.09-6.16c0-1.1-1.1-2.06-2.03-2.67l1.37-2.07c2.6,1.72,3.14,3.52,3.14,4.74,0,5.61-10.6,8.64-20.57,8.64Z"
                                    />
                                    <path
                                        className="cls-6"
                                        d="M252.21,179.79c-9.97,0-20.57-3.03-20.57-8.64h2.48c0,2.91,7.73,6.16,18.09,6.16s18.09-3.26,18.09-6.16h2.48c0,5.61-10.6,8.64-20.57,8.64Z"
                                    />
                                    <path
                                        className="cls-6"
                                        d="M241.24,174.23c-2.24-.57-4.14-1.29-5.67-2.16l1.22-2.16c1.31.75,3.06,1.41,5.06,1.91l-.61,2.4Z"
                                    />
                                    <rect
                                        className="cls-6"
                                        x="231.64"
                                        y="161.83"
                                        width="2.48"
                                        height="9.31"
                                    />
                                    <rect
                                        className="cls-6"
                                        x="270.29"
                                        y="161.83"
                                        width="2.48"
                                        height="9.31"
                                    />
                                </g>
                            </g>
                        </g>

                        {/* Bottom-right tile */}
                        <g>
                            <rect className="cls-2" x="215" y="215" width="85" height="85" />
                            <g>
                                <path
                                    className="cls-6"
                                    d="M245.29,287.64h-15.5c-.43,0-.82-.22-1.05-.58-.23-.36-.25-.81-.07-1.2l16.31-34.08c.2-.43.63-.7,1.11-.7h.01c.47,0,.9.27,1.11.68l7.89,15.77c.17.34.18.74.01,1.09l-8.69,18.31c-.21.43-.64.71-1.12.71ZM231.76,285.16h12.75l8.09-17.06-6.48-12.96-14.36,30.02Z"
                                />
                                <path
                                    className="cls-6"
                                    d="M284.99,287.64h-14.57c-.48,0-.91-.27-1.12-.7l-21.31-44.34h-8.71c-.68,0-1.24-.55-1.24-1.24v-12.56c0-.68.55-1.24,1.24-1.24h18.71c.48,0,.92.28,1.12.71l20.78,44.07h5.1c.68,0,1.24.55,1.24,1.24v12.83c0,.68-.55,1.24-1.24,1.24ZM271.2,285.16h12.55v-10.35h-4.64c-.48,0-.92-.28-1.12-.71l-20.78-44.07h-16.69v10.08h8.25c.48,0,.91.27,1.12.7l21.31,44.34Z"
                                />
                            </g>
                        </g>
                    </g>

                    {/* Invisible path refs for animation (5 variants each) */}
                    <g opacity={0} aria-hidden="true">
                        {/* StreamToLambda */}
                        {[
                            "M150,150c5.57,14.52,15.54,35.2,33.48,55.8,26.79,30.75,57.32,45.23,74.02,51.7",
                            "M150,150c5.57,14.52,12.79,35.94,30.74,56.54,26.79,30.75,60.07,44.49,76.76,50.96",
                            "M150,150c5.57,14.52,10.05,36.69,27.99,57.29,26.79,30.75,62.81,43.74,79.51,50.21",
                            "M150,150c5.57,14.52,7.3,37.43,25.25,58.03,26.79,30.75,65.56,43,82.25,49.47",
                            "M150,150c5.57,14.52,4.55,38.17,22.5,58.77,26.79,30.75,68.3,42.26,85,48.73",
                        ].map((d, idx) => (
                            <path key={`stl-${idx}`} ref={PATH_REFS.StreamToLambda[idx]} d={d} />
                        ))}

                        {/* StreamToDynamo */}
                        {[
                            "M150,150c35.83,0,71.67,0,107.5,0",
                            "M150,150c13.13,0,36.16,1.64,49.29,1.64,22.71,0,35.51-1.64,58.21-1.64",
                            "M150,150c13.13,0,36.16-1.64,49.29-1.64,22.71,0,35.51,1.64,58.21,1.64",
                            "M150,150c13.13,0,36.16,3.27,49.29,3.27,22.71,0,35.51-3.27,58.21-3.27",
                            "M150,150c13.13,0,36.16-3.27,49.29-3.27,22.71,0,35.51,3.27,58.21,3.27",
                        ].map((d, idx) => (
                            <path key={`std-${idx}`} ref={PATH_REFS.StreamToDynamo[idx]} d={d} />
                        ))}

                        {/* MiddleRobotToStream (StreamToDynamo-2 in SVG) */}
                        {[
                            "M43.75,150c35.83,0,71.67,0,107.5,0",
                            "M43.75,150c13.13,0,36.16,1.64,49.29,1.64,22.71,0,35.51-1.64,58.21-1.64",
                            "M43.75,150c13.13,0,36.16-1.64,49.29-1.64,22.71,0,35.51,1.64,58.21,1.64",
                            "M43.75,150c13.13,0,36.16,3.27,49.29,3.27,22.71,0,35.51-3.27,58.21-3.27",
                            "M43.75,150c13.13,0,36.16-3.27,49.29-3.27,22.71,0,35.51,3.27,58.21,3.27",
                        ].map((d, idx) => (
                            <path
                                key={`mrs-${idx}`}
                                ref={PATH_REFS.MiddleRobotToStream[idx]}
                                d={d}
                            />
                        ))}

                        {/* TopRobotToStream (starts at center; to go robot->center use dir=-1) */}
                        {[
                            "M151.25,150c-5.57-14.52-15.54-35.2-33.48-55.8-26.79-30.75-57.32-45.23-74.02-51.7",
                            "M151.25,150c-5.57-14.52-12.79-35.94-30.74-56.54-26.79-30.75-60.07-44.49-76.76-50.96",
                            "M151.25,150c-5.57-14.52-10.05-36.69-27.99-57.29-26.79-30.75-62.81-43.74-79.51-50.21",
                            "M151.25,150c-5.57-14.52-7.3-37.43-25.25-58.03-26.79-30.75-65.56-43-82.25-49.47",
                            "M151.25,150c-5.57-14.52-4.55-38.17-22.5-58.77-26.79-30.75-68.3-42.26-85-48.73",
                        ].map((d, idx) => (
                            <path key={`trs-${idx}`} ref={PATH_REFS.TopRobotToStream[idx]} d={d} />
                        ))}

                        {/* BottomRobotToStream (starts at center; to go robot->center use dir=-1) */}
                        {[
                            "M151.25,150c-5.57,14.52-15.54,35.2-33.48,55.8-26.79,30.75-57.32,45.23-74.02,51.7",
                            "M151.25,150c-5.57,14.52-12.79,35.94-30.74,56.54-26.79,30.75-60.07,44.49-76.76,50.96",
                            "M151.25,150c-5.57,14.52-10.05,36.69-27.99,57.29-26.79,30.75-62.81,43.74-79.51,50.21",
                            "M151.25,150c-5.57,14.52-7.3,37.43-25.25,58.03-26.79,30.75-65.56,43-82.25,49.47",
                            "M151.25,150c-5.57,14.52-4.55,38.17-22.5,58.77-26.79,30.75-68.3,42.26-85,48.73",
                        ].map((d, idx) => (
                            <path key={`brs-${idx}`} ref={PATH_REFS.BottomRobotToStream[idx]} d={d} />
                        ))}
                    </g>

                    {/* Animated bit streams */}
                    <g key={cycleKey}>
                        {phases.flatMap((phase, phaseIdx) => {
                            const durationMs = phase.durationMs ?? DEFAULT_PHASE_MS;
                            const initialDelayMs = delayForPhase(phaseIdx);
                            return phase.routes.map((route) => (
                                <BitStream
                                    key={`${phaseIdx}-${route}`}
                                    pathRefs={PATH_REFS[route]}
                                    dir={phase.dir}
                                    durationMs={durationMs}
                                    initialDelayMs={initialDelayMs}
                                    launchStepMs={launchStepMs}
                                    cycleKey={cycleKey}
                                />
                            ));
                        })}
                    </g>

                    {showLabels && (
                        <g opacity={0.7} aria-hidden="true">
                            {(() => {
                                // Node layout (matches Diagram-41 coordinates)
                                const mac = { x: 43.75, y: 150 }; // left-middle
                                const topClient = { x: 43.75, y: 35 };
                                const bottomClient = { x: 43.75, y: 265 };
                                const stream = { x: 150, y: 150 }; // center
                                const dynamo = { x: 257.5, y: 150 }; // right-middle
                                const lambda = { x: 257.5, y: 257.5 }; // bottom-right

                                return (
                                    <>
                            <text
                                x={topClient.x}
                                y={topClient.y - 22}
                                textAnchor="middle"
                                {...LABEL_TEXT_PROPS}
                            >
                                Client
                            </text>
                            <text
                                x={mac.x}
                                y={mac.y - 22}
                                textAnchor="middle"
                                {...LABEL_TEXT_PROPS}
                            >
                                Mac
                            </text>
                            <text
                                x={bottomClient.x}
                                y={bottomClient.y - 22}
                                textAnchor="middle"
                                {...LABEL_TEXT_PROPS}
                            >
                                Client
                            </text>
                            <text
                                x={stream.x}
                                y={stream.y - 26}
                                textAnchor="middle"
                                {...LABEL_TEXT_PROPS}
                            >
                                Stream
                            </text>
                            <text
                                x={dynamo.x}
                                y={dynamo.y - 26}
                                textAnchor="middle"
                                {...LABEL_TEXT_PROPS}
                            >
                                DynamoDB
                            </text>
                            <text
                                x={lambda.x}
                                y={lambda.y - 26}
                                textAnchor="middle"
                                {...LABEL_TEXT_PROPS}
                            >
                                Lambda
                            </text>
                                    </>
                                );
                            })()}
                        </g>
                    )}

                    {debugShowPhaseOrder && (
                        <g opacity={0.5} aria-hidden="true">
                            <rect
                                x={6}
                                y={6}
                                width={160}
                                height={phases.length * 12 + 12}
                                rx={6}
                                fill="var(--background-color)"
                                stroke="var(--text-color)"
                                strokeOpacity={0.18}
                            />
                            {phases.map((p, idx) => (
                                <text
                                    key={idx}
                                    x={12}
                                    y={22 + idx * 12}
                                    fontFamily="monospace"
                                    fontSize="10"
                                    fill="var(--text-color)"
                                >
                                    {idx + 1}. {p.label ?? `${p.routes.join("+")} (${p.dir})`}
                                </text>
                            ))}
                        </g>
                    )}
                </svg>
            </div>
        );
    };

export default StreamBitsRoutingDiagram;

