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
 * Shared frame component that enforces a 3:4 aspect ratio by smart-cropping
 * around receipt content. Ensures all words and receipt polygons remain visible.
 *
 * Provides crop info to children via render prop so they can adjust their SVG
 * viewBox and transform coordinates accordingly.
 */
const ReceiptBoundingBoxFrame: React.FC<ReceiptBoundingBoxFrameProps> = ({
  children,
  lines,
  receipts,
  imageWidth,
  imageHeight,
}) => {
  const maxDisplayWidth = 520;
  const targetAspectRatio = 3 / 4;

  // Compute smart crop region if we have content
  const cropInfo = useMemo(() => {
    if (lines.length > 0 || receipts.length > 0) {
      return computeSmartCropViewBox(lines, receipts, imageWidth, imageHeight);
    }
    return null;
  }, [lines, receipts, imageWidth, imageHeight]);

  const aspectRatio = cropInfo ? targetAspectRatio : imageWidth / imageHeight;

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        minHeight: 0,
        alignItems: "center",
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: maxDisplayWidth,
          aspectRatio: `${aspectRatio}`,
          borderRadius: "15px",
          overflow: "hidden",
        }}
      >
        {children(cropInfo, imageWidth, imageHeight)}
      </div>
    </div>
  );
};

export default ReceiptBoundingBoxFrame;
