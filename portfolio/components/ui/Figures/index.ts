import dynamic from "next/dynamic";

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

export { ClientImageCounts, ClientReceiptCounts } from "./DataCounts";
export { default as EmbeddingCoordinate } from "./EmbeddingCoordinate";
export { default as EmbeddingExample } from "./EmbeddingExample";
export { default as PhotoReceiptBoundingBox } from "./PhotoReceiptBoundingBox";
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
export { default as CroppedAddressImage } from "./CroppedAddressImage";
export { default as ImageStack } from "./ImageStack";
export { default as RandomReceiptWithLabels } from "./RandomReceiptWithLabels";
