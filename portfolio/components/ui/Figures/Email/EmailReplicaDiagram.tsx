import React from "react";
import { useViewportAnimation } from "../useDiagramOptimizations";
import {
  BitStream,
  ClientIcon,
  EmailGradients,
  FanPaths,
  LABEL_TEXT_PROPS,
  LambdaIcon,
  LaptopIcon,
  Phase,
  S3Icon,
  delayFor,
  makeRefs,
  useCycle,
} from "./EmailBitStream";

interface EmailReplicaDiagramProps {
  /** Optional deterministic 0/1 sequence for SSR/CSR parity. */
  chars?: string[];
  paused?: boolean;
}

type RouteName = "MacToS3" | "S3ToLambda" | "LambdaToClient";

const WIDTH = 600;
const HEIGHT = 200;
const ROW_Y = 92;
const LABEL_Y = 166;

const X_MAC = 62;
const X_S3 = 232;
const X_LAMBDA = 396;
const X_CLIENT = 548;

/**
 * Mac → S3 replica/ → Lambda → MCP client. The SQLite file on the Mac is
 * the primary; the Lambda serves reads from a nightly copy.
 */
const EmailReplicaDiagram: React.FC<EmailReplicaDiagramProps> = ({
  chars,
  paused = false,
}) => {
  const { containerRef, shouldAnimate, springPause } =
    useViewportAnimation(paused);

  const PATH_REFS = React.useMemo(
    () => ({
      MacToS3: makeRefs(),
      S3ToLambda: makeRefs(),
      LambdaToClient: makeRefs(),
    }),
    [],
  );

  const TIMELINE = React.useMemo<Phase<RouteName>[]>(
    () => [
      { paths: ["MacToS3"], dir: 1, duration: 700 }, // nightly upload
      { paths: ["S3ToLambda"], dir: 1, duration: 600 }, // download once
      { paths: ["LambdaToClient"], dir: -1, duration: 350 }, // question
      { paths: ["LambdaToClient"], dir: 1, duration: 350 }, // answer
      { paths: ["LambdaToClient"], dir: -1, duration: 350 }, // question
      { paths: ["LambdaToClient"], dir: 1, duration: 350 }, // answer
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
        aria-label="The Mac publishes a SQLite replica to S3; a Lambda downloads it and answers MCP clients"
        style={{ maxWidth: "100%", height: "auto" }}
      >
        <EmailGradients />

        <LaptopIcon x={X_MAC} y={ROW_Y} />
        <text x={X_MAC} y={LABEL_Y} {...LABEL_TEXT_PROPS}>
          Mac (primary)
        </text>

        <S3Icon x={X_S3} y={ROW_Y} gradientId="email-s3-gradient" />
        <text x={X_S3} y={LABEL_Y} {...LABEL_TEXT_PROPS}>
          S3 replica/
        </text>

        <LambdaIcon x={X_LAMBDA} y={ROW_Y} gradientId="email-lambda-gradient" />
        <text x={X_LAMBDA} y={LABEL_Y} {...LABEL_TEXT_PROPS}>
          Lambda
        </text>

        <ClientIcon x={X_CLIENT} y={ROW_Y} />
        <text x={X_CLIENT} y={LABEL_Y} {...LABEL_TEXT_PROPS}>
          MCP
        </text>

        {/* Hidden rails */}
        <FanPaths refs={PATH_REFS.MacToS3} x1={118} y1={ROW_Y} x2={187} y2={ROW_Y} />
        <FanPaths refs={PATH_REFS.S3ToLambda} x1={277} y1={ROW_Y} x2={351} y2={ROW_Y} />
        <FanPaths refs={PATH_REFS.LambdaToClient} x1={441} y1={ROW_Y} x2={526} y2={ROW_Y} />

        <g id="email-replica-bits" key={cycle} fontFamily="monospace" fontSize="12">
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

export default EmailReplicaDiagram;
