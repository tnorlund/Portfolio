import React, { useMemo } from "react";
import type { Line } from "../../../types/api";
import { computeSmartCropViewBox, type CropViewBox, type ReceiptPolygon } from "./utils/smartCrop";

interface ReceiptBoundingBoxFrameProps {
  children: (cropInfo: CropViewBox | null, fullImageWidth: number, fullImageHeight: number) => React.ReactNode;
  lines: Line[];
  receipts: ReceiptPolygon[];
  imageWidth: number;
  imageHeight: number;
}

/**
 * Shared frame component that enforces a 3:4 aspect ratio.
 * Uses the padding-top trick for reliable aspect ratio across all browsers.
 */
const ReceiptBoundingBoxFrame: React.FC<ReceiptBoundingBoxFrameProps> = ({
  children,
  lines,
  receipts,
  imageWidth,
  imageHeight,
}) => {
  const maxDisplayWidth = 520;
  const targetAspectRatio = 3 / 4; // width / height

  // Compute smart crop region if we have content
  const cropInfo = useMemo(() => {
    if (lines.length > 0 || receipts.length > 0) {
      return computeSmartCropViewBox(lines, receipts, imageWidth, imageHeight);
    }
    return null;
  }, [lines, receipts, imageWidth, imageHeight]);

  // Padding-top percentage for aspect ratio: (height / width) * 100 = (1 / aspectRatio) * 100
  const paddingTopPercent = (1 / targetAspectRatio) * 100;

  return (
    <div
      style={{
        position: "relative",
        width: "100%",
        maxWidth: maxDisplayWidth,
        paddingTop: `${paddingTopPercent}%`,
        borderRadius: "15px",
        overflow: "hidden",
        backgroundColor: "var(--code-background)",
      }}
    >
      {/* Content fills the container */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
        }}
      >
        {children(cropInfo, imageWidth, imageHeight)}
      </div>
    </div>
  );
};

export default ReceiptBoundingBoxFrame;
