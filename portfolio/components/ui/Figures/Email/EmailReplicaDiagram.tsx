import React from "react";
import {
  ClaudeIcon,
  LambdaIcon,
  LaptopIcon,
  S3Icon,
} from "./EmailBitStream";
import EmailFlowDiagram, { FlowLeg, FlowNode } from "./EmailFlowDiagram";

interface EmailReplicaDiagramProps {
  chars?: string[];
  paused?: boolean;
}

const NODES: FlowNode[] = [
  {
    id: "mac",
    label: "Mac (primary)",
    render: (x, y) => <LaptopIcon x={x} y={y} />,
  },
  {
    id: "s3",
    label: "S3 replica/",
    render: (x, y) => <S3Icon x={x} y={y} gradientId="email-s3-gradient" />,
  },
  {
    id: "lambda",
    label: "Lambda",
    render: (x, y) => (
      <LambdaIcon x={x} y={y} gradientId="email-lambda-gradient" />
    ),
  },
  { id: "claude", label: "Claude", render: (x, y) => <ClaudeIcon x={x} y={y} /> },
];

/**
 * Mac → S3 (nightly upload), S3 → Lambda (download once), then the client
 * asks and the Lambda answers, twice.
 */
const LEGS: FlowLeg[] = [
  { from: 0, to: 1 },
  { from: 1, to: 2 },
  { from: 3, to: 2, duration: 350 },
  { from: 2, to: 3, duration: 350 },
  { from: 3, to: 2, duration: 350 },
  { from: 2, to: 3, duration: 350 },
];

const EmailReplicaDiagram: React.FC<EmailReplicaDiagramProps> = ({
  chars,
  paused,
}) => (
  <EmailFlowDiagram
    nodes={NODES}
    legs={LEGS}
    chars={chars}
    paused={paused}
    ariaLabel="The Mac uploads a SQLite snapshot to S3, a Lambda downloads it, and Claude asks it questions over MCP"
  />
);

export default EmailReplicaDiagram;
