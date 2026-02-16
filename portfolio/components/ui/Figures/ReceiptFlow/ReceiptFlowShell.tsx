import React from "react";
import styles from "./ReceiptFlowShell.module.css";

interface ReceiptFlowShellProps {
  queue: React.ReactNode;
  center: React.ReactNode;
  legend: React.ReactNode;
  flying?: React.ReactNode;
  next?: React.ReactNode;
  isTransitioning: boolean;
  layoutVars?: React.CSSProperties;
}

export const ReceiptFlowShell: React.FC<ReceiptFlowShellProps> = ({
  queue,
  center,
  legend,
  flying,
  next,
  isTransitioning,
  layoutVars,
}) => {
  return (
    <div className={styles.mainWrapper} style={layoutVars}>
      <div className={styles.queuePane}>{queue}</div>

      <div className={styles.centerColumn}>
        <div className={`${styles.receiptContainer} ${isTransitioning ? styles.fadeOut : ""}`}>
          {center}
        </div>

        <div className={styles.flyingReceiptContainer}>{flying}</div>

        {next ? (
          <div className={`${styles.receiptContainer} ${styles.nextReceipt} ${isTransitioning ? styles.fadeIn : ""}`}>
            {next}
          </div>
        ) : null}
      </div>

      {legend}
    </div>
  );
};
