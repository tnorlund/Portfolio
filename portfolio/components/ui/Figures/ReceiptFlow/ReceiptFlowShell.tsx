import React from "react";
import styles from "./ReceiptFlowShell.module.css";

interface ReceiptFlowShellProps {
  queue: React.ReactNode;
  center: React.ReactNode;
  legend: React.ReactNode;
  flying?: React.ReactNode;
  next?: React.ReactNode;
  nextLegend?: React.ReactNode;
  isTransitioning: boolean;
  stabilizeLegend?: boolean;
  layoutVars?: React.CSSProperties;
}

export const ReceiptFlowShell: React.FC<ReceiptFlowShellProps> = ({
  queue,
  center,
  legend,
  flying,
  next,
  nextLegend,
  isTransitioning,
  stabilizeLegend = false,
  layoutVars,
}) => {
  const useLegendOverlay = stabilizeLegend || Boolean(nextLegend);

  return (
    <div className={styles.mainWrapper} style={layoutVars} data-rf-shell>
      <div className={styles.queuePane}>{queue}</div>

      <div className={styles.centerColumn}>
        <div className={`${styles.receiptContainer} ${isTransitioning ? styles["fade-out"] : ""}`}>
          {center}
        </div>

        <div className={styles.flyingReceiptContainer} data-rf-target>{flying}</div>

        {next ? (
          <div className={`${styles.receiptContainer} ${styles.nextReceipt} ${isTransitioning ? styles["fade-in"] : ""}`}>
            {next}
          </div>
        ) : null}
      </div>

      <div
        className={`${styles.legendColumn} ${useLegendOverlay ? styles.legendColumnOverlay : ""}`}
      >
        <div
          className={`${styles.currentLegend} ${isTransitioning && nextLegend ? styles["fade-out"] : ""}`}
        >
          {legend}
        </div>
        {nextLegend ? (
          <div className={`${styles.nextLegend} ${isTransitioning ? styles["fade-in"] : ""}`}>
            {nextLegend}
          </div>
        ) : null}
      </div>
    </div>
  );
};
