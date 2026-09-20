import React from "react";
import {
  EnvelopeIcon,
  GrokIcon,
  LambdaIcon,
  SesIcon,
} from "./EmailBitStream";
import EmailFlowDiagram, { FlowLeg, FlowNode } from "./EmailFlowDiagram";

interface EmailCodeDiagramProps {
  chars?: string[];
  paused?: boolean;
}

/** The eight characters that survive the Lambda. */
const CODE = "K7Q29XA1".split("");

const NODES: FlowNode[] = [
  {
    id: "inbox",
    label: "Inbox",
    render: (x, y) => <EnvelopeIcon x={x} y={y} scale={1.3} />,
  },
  { id: "ses", label: "SES", render: (x, y) => <SesIcon x={x} y={y} /> },
  {
    id: "lambda",
    label: "Lambda",
    render: (x, y) => (
      <LambdaIcon x={x} y={y} gradientId="email-lambda-gradient" />
    ),
  },
  { id: "bot", label: "Grok Bot", render: (x, y) => <GrokIcon x={x} y={y} /> },
];

/**
 * Inbox → SES → Lambda → Grok Bot. Up to the Lambda the bits are an email;
 * after it, the trail is literally the code: eight glyphs, K7Q29XA1.
 */
const LEGS: FlowLeg[] = [
  { from: 0, to: 1 },
  { from: 1, to: 2 },
  { from: 2, to: 3, count: CODE.length, chars: CODE, launch: 70 },
];

const EmailCodeDiagram: React.FC<EmailCodeDiagramProps> = ({
  chars,
  paused,
}) => (
  <EmailFlowDiagram
    nodes={NODES}
    legs={LEGS}
    chars={chars}
    paused={paused}
    ariaLabel="A Greenhouse email is forwarded to SES, a Lambda extracts the eight-character code, and only the code reaches the job bot"
  />
);

export default EmailCodeDiagram;
