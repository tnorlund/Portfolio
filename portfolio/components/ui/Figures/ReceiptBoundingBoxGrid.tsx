import React from "react";
import ClientOnly from "../../ClientOnly";
import {
  ZDepthConstrainedParametric,
  ZDepthUnconstrainedParametric,
} from "./IsometricPlane";
import PhotoReceiptBoundingBox from "./PhotoReceiptBoundingBox";
import ScanReceiptBoundingBox from "./ScanReceiptBoundingBox";
import styles from "./ReceiptBoundingBoxGrid.module.css";

/**
 * Grid component that displays four figures in a 2x2 layout:
 * - Top left: ZDepthConstrainedParametric
 * - Top right: ZDepthUnconstrainedParametric
 * - Bottom left: ScanReceiptBoundingBox
 * - Bottom right: PhotoReceiptBoundingBox
 */
const ReceiptBoundingBoxGrid: React.FC = () => {
  return (
    <div className={styles.grid} data-testid="receipt-bounding-box-grid">
      <div className={styles.gridCell}>
        <ClientOnly>
          <ZDepthConstrainedParametric />
        </ClientOnly>
      </div>
      <div className={styles.gridCell}>
        <ClientOnly>
          <ZDepthUnconstrainedParametric />
        </ClientOnly>
      </div>
      <div className={styles.gridCell} data-testid="scan-receipt-cell">
        <ScanReceiptBoundingBox />
      </div>
      <div className={styles.gridCell} data-testid="photo-receipt-cell">
        <PhotoReceiptBoundingBox />
      </div>
    </div>
  );
};

export default ReceiptBoundingBoxGrid;
