import React from "react";
import { EvaluatorCarousel } from "../TwoTierFlow";
import type { CarouselConfig } from "../TwoTierFlow";

const config: CarouselConfig = {
  tier1: {
    name: "Auto-Resolve",
    color: "var(--color-blue)",
    type: "Deterministic",
  },
  tier2: {
    name: "ChromaDB Consensus",
    color: "var(--color-purple)",
    type: "Consensus",
  },
  getEvaluation: (receipt) => receipt.metadata,
  scanLineColors: ["var(--color-blue)", "var(--color-purple)"],
};

const MetadataVisualization: React.FC = () => {
  return <EvaluatorCarousel config={config} />;
};

export default MetadataVisualization;
