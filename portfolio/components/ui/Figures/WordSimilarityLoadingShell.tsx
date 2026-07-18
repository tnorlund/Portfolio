import React from "react";
import styles from "./WordSimilarity.module.css";

/**
 * Stable first-paint shell for the word-similarity figure.
 *
 * This lives outside the dynamically imported figure so Next can render the
 * same geometry while the component chunk and API payload are both loading.
 */
export const WordSimilarityLoadingShell: React.FC = () => (
  <div
    className={styles.loadingShell}
    data-testid="word-similarity"
    aria-busy="true"
    aria-label="Loading word similarity results"
  >
    <div className={styles.receiptStage} aria-hidden="true">
      {Array.from({ length: 6 }).map((_, index) => (
        <div key={index} className={styles.receiptSkeleton} />
      ))}
    </div>

    <div className={styles.tableSkeleton} aria-hidden="true">
      <div className={styles.tableHeaderSkeleton} />
      {Array.from({ length: 5 }).map((_, index) => (
        <div key={index} className={styles.tableRowSkeleton}>
          <span />
          <span />
          <span />
        </div>
      ))}
    </div>

    <div className={styles.timingSkeleton} aria-hidden="true">
      <div className={styles.timingTitleSkeleton} />
      <div className={styles.timingBarSkeleton} />
      <div className={styles.timingLegendSkeleton}>
        {Array.from({ length: 5 }).map((_, index) => (
          <span key={index} />
        ))}
      </div>
    </div>
  </div>
);

export default WordSimilarityLoadingShell;
