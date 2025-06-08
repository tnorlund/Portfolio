import dynamic from "next/dynamic";

// Make MerchantCount and LabelValidationCount client-side only to prevent SSR issues during static export
const ClientMerchantCount = dynamic(() => import("./MerchantCount"), {
  ssr: false,
});

const ClientLabelValidationCount = dynamic(
  () => import("./LabelValidationCount"),
  {
    ssr: false,
  },
);

export { default as ZDepthConstrained } from "./ZDepthConstrained";
export { default as ZDepthUnconstrained } from "./ZDepthUnconstrained";
export { default as UploadDiagram } from "./UploadDiagram";
export { default as EmbeddingExample } from "./EmbeddingExample";
export { default as EmbeddingCoordinate } from "./EmbeddingCoordinate";
export { ClientImageCounts, ClientReceiptCounts } from "./DataCounts";
export { ClientMerchantCount as MerchantCount };
export { default as ReceiptStack } from "./ReceiptStack";
export { ClientLabelValidationCount as LabelValidationCount };
export { default as ReceiptBoundingBox } from "./ReceiptBoundingBox";
export const ReceiptPhotoClustering = dynamic(
  () => import("./ReceiptPhotoClustering"),
  { ssr: false },
);
