import dynamic from "next/dynamic";
import React from "react";
import { ReceiptFlowLoadingShell } from "./ReceiptFlow/ReceiptFlowLoadingShell";

function loadingShell(variant: React.ComponentProps<typeof ReceiptFlowLoadingShell>["variant"]) {
  return React.createElement(ReceiptFlowLoadingShell, { variant });
}

// Make MerchantCount and LabelValidationCount client-side only to prevent SSR issues during static export
const ClientMerchantCount = dynamic(() => import("./MerchantCount"), {
  ssr: false,
});

const ClientLabelValidationCount = dynamic(
  () => import("./LabelValidationCount"),
  {
    ssr: false,
  }
);

export { default as AWSFlowDiagram } from "./AWSFlowDiagram";
export { default as CodeBuildDiagram } from "./CodeBuildDiagram";
export { ClientImageCounts, ClientReceiptCounts } from "./DataCounts";
export { default as EmbeddingCoordinate } from "./EmbeddingCoordinate";
export { default as EmbeddingExample } from "./EmbeddingExample";
export {
  default as IsometricPlane,
  ZDepthConstrainedParametric,
  ZDepthUnconstrainedParametric
} from "./IsometricPlane";
export { default as LockingSwimlane } from "./LockingSwimlane";
export { default as PhotoReceiptBoundingBox } from "./PhotoReceiptBoundingBox";
export { default as ReceiptBoundingBoxGrid } from "./ReceiptBoundingBoxGrid";
export { default as ReceiptStack } from "./ReceiptStack";
export { default as ScanReceiptBoundingBox } from "./ScanReceiptBoundingBox";
export { default as UploadDiagram } from "./UploadDiagram";
export { default as ZDepthConstrained } from "./ZDepthConstrained";
export { default as ZDepthUnconstrained } from "./ZDepthUnconstrained";
export { ClientLabelValidationCount as LabelValidationCount, ClientMerchantCount as MerchantCount };
export const ReceiptPhotoClustering = dynamic(
  () => import("./ReceiptPhotoClustering"),
  { ssr: false }
);
export const PhotoReceiptDBSCAN = dynamic(
  () => import("./PhotoReceiptDBSCAN"),
  { ssr: false }
);
export { default as AddressSimilarity } from "./AddressSimilarity";
export { default as AddressSimilaritySideBySide } from "./AddressSimilaritySideBySide";
export { default as WordSimilarity } from "./WordSimilarity";
export { default as CICDLoop } from "./CICDLoop";
export { default as CroppedAddressImage } from "./CroppedAddressImage";
export { default as DynamoStreamAnimation } from "./DynamoStreamAnimation";
export { default as ImageStack } from "./ImageStack";
export const LayoutLMInferenceVisualization = dynamic(
  () => import("./LayoutLMBatchVisualization"),
  {
    ssr: false,
    loading: () => loadingShell("layoutlm"),
  }
);
export { default as PageCurlLetter } from "./PageCurlLetter";
export { default as PrecisionRecallDartboard } from "./PrecisionRecallDartboard";
export { default as RandomReceiptWithLabels } from "./RandomReceiptWithLabels";
export { default as StreamBitsRoutingDiagram } from "./StreamBitsRoutingDiagram";
export const TrainingMetricsAnimation = dynamic(
  () => import("./TrainingMetricsAnimation"),
  { ssr: false }
);
export const LayoutLMBatchVisualization = dynamic(
  () => import("./LayoutLMBatchVisualization"),
  {
    ssr: false,
    loading: () => loadingShell("layoutlm"),
  }
);
export const LabelValidationTimeline = dynamic(
  () => import("./LabelValidationTimeline"),
  { ssr: false }
);
export const LabelEvaluatorVisualization = dynamic(
  () => import("./LabelEvaluatorVisualization"),
  { ssr: false }
);
export const LabelWordCloud = dynamic(
  () => import("./LabelWordCloud"),
  { ssr: false }
);
export { default as QuestionMarquee } from "./QuestionMarquee";
export const QAAgentFlow = dynamic(
  () => import("./QAAgentFlow"),
  { ssr: false }
);
export const LabelValidationVisualization = dynamic(
  () => import("./LabelValidationVisualization"),
  { ssr: false }
);
export const BetweenReceiptVisualization = dynamic(
  () => import("./BetweenReceiptVisualization"),
  {
    ssr: false,
    loading: () => loadingShell("between"),
  }
);
export const FinancialMathOverlay = dynamic(
  () => import("./FinancialMathOverlay"),
  {
    ssr: false,
    loading: () => loadingShell("financial"),
  }
);
export const WithinReceiptVerification = dynamic(
  () => import("./WithinReceiptVerification"),
  {
    ssr: false,
    loading: () => loadingShell("within"),
  }
);
