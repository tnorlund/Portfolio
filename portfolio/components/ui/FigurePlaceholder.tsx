import React from "react";
import styles from "./FigurePlaceholder.module.css";

export type FigurePlaceholderVariant =
  | "receiptFlow"
  | "metrics"
  | "layoutlm"
  | "wordSimilarity"
  | "pipeline";

interface FigurePlaceholderProps {
  variant: FigurePlaceholderVariant;
}

const FigurePlaceholder: React.FC<FigurePlaceholderProps> = ({ variant }) => {
  if (variant === "layoutlm") {
    return (
      <div className={styles.container} aria-hidden="true">
        <div className={styles.grid}>
          <div className={styles.pillRow}>
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className={styles.pill} />
            ))}
          </div>
          <div className={styles.panelRow}>
            <div className={styles.panel} />
            <div className={`${styles.panel} ${styles.panelTall}`} />
            <div className={styles.panel} />
          </div>
        </div>
      </div>
    );
  }

  if (variant === "metrics") {
    return (
      <div className={styles.container} aria-hidden="true">
        <div className={styles.grid}>
          <div className={`${styles.line} ${styles.lineShort}`} />
          <div className={`${styles.line} ${styles.lineLong}`} />
          <div className={styles.panelRow}>
            <div className={styles.panel} />
            <div className={`${styles.panel} ${styles.panelTall}`} />
            <div className={`${styles.panel} ${styles.panelTall}`} />
          </div>
        </div>
      </div>
    );
  }

  if (variant === "wordSimilarity") {
    return (
      <div className={styles.container} aria-hidden="true">
        <div className={styles.grid}>
          <div className={`${styles.line} ${styles.lineMed}`} />
          <div className={styles.panelRow}>
            <div className={styles.panel} />
            <div className={`${styles.panel} ${styles.panelTall}`} />
            <div className={styles.panel} />
          </div>
        </div>
      </div>
    );
  }

  if (variant === "pipeline") {
    return (
      <div className={styles.container} aria-hidden="true">
        <div className={styles.grid}>
          <div className={`${styles.line} ${styles.lineShort}`} />
          <div className={styles.pillRow}>
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className={styles.pill} />
            ))}
          </div>
          <div className={styles.panelRow}>
            <div className={styles.panel} />
            <div className={`${styles.panel} ${styles.panelTall}`} />
            <div className={styles.panel} />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.container} aria-hidden="true">
      <div className={styles.grid}>
        <div className={`${styles.line} ${styles.lineShort}`} />
        <div className={`${styles.line} ${styles.lineMed}`} />
        <div className={styles.panelRow}>
          <div className={styles.panel} />
          <div className={`${styles.panel} ${styles.panelTall}`} />
          <div className={styles.panel} />
        </div>
      </div>
    </div>
  );
};

export default FigurePlaceholder;
