import React from "react";
import { useViewportAnimation } from "../useDiagramOptimizations";
import {
  BitStream,
  EmailGradients,
  EnvelopeIcon,
  FanPaths,
  LABEL_TEXT_PROPS,
  Phase,
  S3Icon,
  delayFor,
  makeRefs,
  useCycle,
} from "./EmailBitStream";

interface EmailInboxDiagramProps {
  /** Optional deterministic 0/1 sequence for SSR/CSR parity. */
  chars?: string[];
  paused?: boolean;
}

type RouteName = "InboxToSes" | "SesToS3";

const WIDTH = 360;
const HEIGHT = 190;
const ROW_Y = 88;
const LABEL_Y = 158;

/**
 * Inbox → SES → S3. Mail is forwarded to receipts@in.tylernorlund.com,
 * SES scans it and drops the raw message under raw/ in a private bucket.
 */
const EmailInboxDiagram: React.FC<EmailInboxDiagramProps> = ({
  chars,
  paused = false,
}) => {
  const { containerRef, shouldAnimate, springPause } =
    useViewportAnimation(paused);

  const PATH_REFS = React.useMemo(
    () => ({
      InboxToSes: makeRefs(),
      SesToS3: makeRefs(),
    }),
    [],
  );

  const TIMELINE = React.useMemo<Phase<RouteName>[]>(
    () => [
      { paths: ["InboxToSes"], dir: 1 }, // forward: inbox → SES
      { paths: ["SesToS3"], dir: 1 }, // store: SES → S3 raw/
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
        aria-label="Email forwarded from the inbox to Amazon SES, then stored in S3"
        style={{ maxWidth: "100%", height: "auto" }}
      >
        <EmailGradients />

        {/* Inbox */}
        <EnvelopeIcon x={52} y={ROW_Y} scale={1.1} />
        <text x={52} y={LABEL_Y} {...LABEL_TEXT_PROPS}>
          Inbox
        </text>

        {/* SES */}
        <g transform={`translate(${180 - 42.5},${ROW_Y - 42.5})`}>
          <rect width="85" height="85" rx="6" fill="url(#email-ses-gradient)" />
        </g>
        <EnvelopeIcon x={180} y={ROW_Y} fill="white" stroke="#b0084d" scale={0.8} />
        <text x={180} y={LABEL_Y} {...LABEL_TEXT_PROPS}>
          SES
        </text>

        {/* S3 */}
        <S3Icon x={308} y={ROW_Y} gradientId="email-s3-gradient" />
        <text x={308} y={LABEL_Y} {...LABEL_TEXT_PROPS}>
          S3 raw/
        </text>

        {/* Hidden rails */}
        <FanPaths refs={PATH_REFS.InboxToSes} x1={86} y1={ROW_Y} x2={135} y2={ROW_Y} />
        <FanPaths refs={PATH_REFS.SesToS3} x1={225} y1={ROW_Y} x2={263} y2={ROW_Y} />

        {/* Animated bits */}
        <g id="email-inbox-bits" key={cycle} fontFamily="monospace" fontSize="12">
          {TIMELINE.map((phase, phaseIdx) =>
            phase.paths.map((name) => (
              <BitStream
                key={`${phaseIdx}-${name}`}
                pathRefs={PATH_REFS[name]}
                dir={phase.dir}
                duration={phase.duration}
                launch={phase.launch}
                initialDelay={delayFor(TIMELINE, phaseIdx)}
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

export default EmailInboxDiagram;
