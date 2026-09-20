import React from "react";
import { EnvelopeIcon, S3Icon, SesIcon } from "./EmailBitStream";
import EmailFlowDiagram, { FlowLeg, FlowNode } from "./EmailFlowDiagram";

interface EmailInboxDiagramProps {
  chars?: string[];
  paused?: boolean;
}

const NODES: FlowNode[] = [
  {
    id: "inbox",
    label: "Inbox",
    render: (x, y) => <EnvelopeIcon x={x} y={y} scale={1.3} />,
  },
  { id: "ses", label: "SES", render: (x, y) => <SesIcon x={x} y={y} /> },
  {
    id: "s3",
    label: "S3 raw/",
    render: (x, y) => <S3Icon x={x} y={y} gradientId="email-s3-gradient" />,
  },
];

/** Inbox → SES → S3 raw/. Mail is forwarded, scanned, and archived. */
const LEGS: FlowLeg[] = [
  { from: 0, to: 1 },
  { from: 1, to: 2 },
];

const EmailInboxDiagram: React.FC<EmailInboxDiagramProps> = ({
  chars,
  paused,
}) => (
  <EmailFlowDiagram
    nodes={NODES}
    legs={LEGS}
    chars={chars}
    paused={paused}
    ariaLabel="Email forwarded from the inbox to Amazon SES, then stored in S3"
  />
);

export default EmailInboxDiagram;
