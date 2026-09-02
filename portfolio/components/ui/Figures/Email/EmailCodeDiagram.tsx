import React from "react";
import { useViewportAnimation } from "../useDiagramOptimizations";
import {
  BitStream,
  ClientIcon,
  EmailGradients,
  EnvelopeIcon,
  FanPaths,
  LABEL_TEXT_PROPS,
  LambdaIcon,
  Phase,
  delayFor,
  makeRefs,
  useCycle,
} from "./EmailBitStream";

interface EmailCodeDiagramProps {
  chars?: string[];
  paused?: boolean;
}

type RouteName = "InboxToSes" | "SesToLambda" | "LambdaToCode" | "CodeToBot";

const WIDTH = 700;
const HEIGHT = 190;
const ROW_Y = 88;
const LABEL_Y = 158;
const X = { inbox: 44, ses: 196, lambda: 348, code: 490, bot: 640 };

/**
 * The job-search path. One Greenhouse email → SES → a Lambda that keeps only
 * the eight-character code → the bot. The bits stop being an email at the
 * Lambda: what crosses the last gap is the code, nothing else.
 */
const EmailCodeDiagram: React.FC<EmailCodeDiagramProps> = ({
  chars,
  paused = false,
}) => {
  const { containerRef, shouldAnimate, springPause } =
    useViewportAnimation(paused);

  const PATH_REFS = React.useMemo(
    () => ({
      InboxToSes: makeRefs(),
      SesToLambda: makeRefs(),
      LambdaToCode: makeRefs(),
      CodeToBot: makeRefs(),
    }),
    [],
  );

  const TIMELINE = React.useMemo<Phase<RouteName>[]>(
    () => [
      { paths: ["InboxToSes"], dir: 1 },
      { paths: ["SesToLambda"], dir: 1 },
      // Past the Lambda only the code travels: eight glyphs, not a stream.
      { paths: ["LambdaToCode"], dir: 1, duration: 350, count: 8 },
      { paths: ["CodeToBot"], dir: 1, duration: 400, count: 8 },
    ],
    [],
  );

  const cycle = useCycle(TIMELINE, shouldAnimate);

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
      <svg
        width={WIDTH}
        height={HEIGHT}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label="A Greenhouse email is forwarded to SES, a Lambda extracts the eight-character code, and only the code reaches the job bot"
        style={{ maxWidth: "100%", height: "auto" }}
      >
        <EmailGradients />

        {/* Inbox */}
        <EnvelopeIcon x={X.inbox} y={ROW_Y} scale={1.1} />
        <text x={X.inbox} y={LABEL_Y} {...LABEL_TEXT_PROPS}>
          Inbox
        </text>

        {/* SES */}
        <g transform={`translate(${X.ses - 42.5},${ROW_Y - 42.5})`}>
          <rect width="85" height="85" rx="6" fill="url(#email-ses-gradient)" />
        </g>
        <EnvelopeIcon x={X.ses} y={ROW_Y} fill="white" stroke="#b0084d" scale={0.8} />
        <text x={X.ses} y={LABEL_Y} {...LABEL_TEXT_PROPS}>
          SES
        </text>

        {/* Lambda */}
        <LambdaIcon x={X.lambda} y={ROW_Y} gradientId="email-lambda-gradient" />
        <text x={X.lambda} y={LABEL_Y} {...LABEL_TEXT_PROPS}>
          Lambda
        </text>

        {/* The code: all that survives */}
        <g transform={`translate(${X.code},${ROW_Y})`}>
          <rect
            x="-44"
            y="-20"
            width="88"
            height="40"
            rx="8"
            fill="var(--code-background)"
            stroke="var(--text-color)"
            strokeWidth="2"
          />
          <text
            textAnchor="middle"
            dominantBaseline="middle"
            fontFamily="monospace"
            fontSize="15"
            fontWeight={700}
            fill="var(--text-color)"
            letterSpacing="1"
          >
            K7Q29XA1
          </text>
        </g>
        <text x={X.code} y={LABEL_Y} {...LABEL_TEXT_PROPS}>
          one code, one hour
        </text>

        {/* The bot */}
        <ClientIcon x={X.bot} y={ROW_Y} />
        <text x={X.bot} y={LABEL_Y} {...LABEL_TEXT_PROPS}>
          Grok Bot
        </text>

        {/* Hidden rails */}
        <FanPaths refs={PATH_REFS.InboxToSes} x1={X.inbox + 34} y1={ROW_Y} x2={X.ses - 45} y2={ROW_Y} />
        <FanPaths refs={PATH_REFS.SesToLambda} x1={X.ses + 45} y1={ROW_Y} x2={X.lambda - 45} y2={ROW_Y} />
        <FanPaths refs={PATH_REFS.LambdaToCode} x1={X.lambda + 45} y1={ROW_Y} x2={X.code - 46} y2={ROW_Y} />
        <FanPaths refs={PATH_REFS.CodeToBot} x1={X.code + 46} y1={ROW_Y} x2={X.bot - 22} y2={ROW_Y} />

        <g id="email-code-bits" key={cycle} fontFamily="monospace" fontSize="12">
          {TIMELINE.map((phase, phaseIdx) =>
            phase.paths.map((name) => (
              <BitStream
                key={`${phaseIdx}-${name}`}
                pathRefs={PATH_REFS[name]}
                dir={phase.dir}
                duration={phase.duration}
                launch={phase.launch}
                initialDelay={delayFor(TIMELINE, phaseIdx)}
                count={phase.count}
                chars={chars}
                pause={springPause}
              />
            )),
          )}
        </g>
      </svg>
    </div>
  );
};

export default EmailCodeDiagram;
