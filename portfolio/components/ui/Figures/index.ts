import dynamic from "next/dynamic";
import React from "react";
import { ReceiptFlowLoadingShell } from "./ReceiptFlow/ReceiptFlowLoadingShell";

type LoadingVariant = React.ComponentProps<typeof ReceiptFlowLoadingShell>["variant"];

const RECEIPT_FLOW_CONTAINER_STYLE: React.CSSProperties = {
  width: "100%",
  maxWidth: "1024px",
  margin: "2rem auto",
  padding: "2rem",
  background: "var(--background-color)",
  borderRadius: "12px",
};

const LAYOUTLM_CONTAINER_STYLE: React.CSSProperties = {
  width: "100%",
  maxWidth: "1000px",
  margin: "2rem auto",
  padding: "1.5rem",
  background: "var(--background-color)",
  borderRadius: "12px",
};

function loadingShell(variant: LoadingVariant) {
  const containerStyle =
    variant === "layoutlm" ? LAYOUTLM_CONTAINER_STYLE : RECEIPT_FLOW_CONTAINER_STYLE;
  const layoutVars = variant === "layoutlm"
    ? ({ "--rf-align-items": "center" } as React.CSSProperties)
    : undefined;

  return React.createElement(
    "div",
    { style: containerStyle },
    React.createElement(ReceiptFlowLoadingShell, { variant, layoutVars })
  );
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

export const AWSFlowDiagram = dynamic(() => import("./AWSFlowDiagram"), { ssr: false });
export const CodeBuildDiagram = dynamic(() => import("./CodeBuildDiagram"), { ssr: false });
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
export const ReceiptBoundingBoxGrid = dynamic(() => import("./ReceiptBoundingBoxGrid"), { ssr: false });
export const ReceiptStack = dynamic(() => import("./ReceiptStack"), { ssr: false });
export { default as ScanReceiptBoundingBox } from "./ScanReceiptBoundingBox";
export const UploadDiagram = dynamic(() => import("./UploadDiagram"), { ssr: false });
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
export const AddressSimilaritySideBySide = dynamic(() => import("./AddressSimilaritySideBySide"), { ssr: false });
export const WordSimilarity = dynamic(() => import("./WordSimilarity"), { ssr: false });
export const CICDLoop = dynamic(() => import("./CICDLoop"), { ssr: false });
export { default as CroppedAddressImage } from "./CroppedAddressImage";
export const DynamoStreamAnimation = dynamic(() => import("./DynamoStreamAnimation"), { ssr: false });
export { default as ImageStack } from "./ImageStack";
const DynamicLayoutLMInferenceVisualization = dynamic(
  () => import("./LayoutLMBatchVisualization"),
  {
    ssr: false,
    loading: () => loadingShell("layoutlm"),
  }
);
export const LayoutLMInferenceVisualization = DynamicLayoutLMInferenceVisualization;
export const PageCurlLetter = dynamic(() => import("./PageCurlLetter"), { ssr: false });
export const PrecisionRecallDartboard = dynamic(() => import("./PrecisionRecallDartboard"), { ssr: false });
export { default as RandomReceiptWithLabels } from "./RandomReceiptWithLabels";
export const StreamBitsRoutingDiagram = dynamic(() => import("./StreamBitsRoutingDiagram"), { ssr: false });
export const TrainingMetricsAnimation = dynamic(
  () => import("./TrainingMetricsAnimation"),
  { ssr: false }
);
export const LabelValidationTimeline = dynamic(
  () => import("./LabelValidationTimeline"),
  { ssr: false }
);
export const MetadataVisualization = dynamic(
  () => import("./MetadataVisualization"),
  { ssr: false }
);
export const CurrencyVisualization = dynamic(
  () => import("./CurrencyVisualization"),
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
