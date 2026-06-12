import React from "react";
import styles from "./WithinReceiptVerification.module.css";

type PassName = "place" | "format";
type PassState = Record<PassName, number>;

const PassIndicator: React.FC<{
  passState?: PassState;
  activePass?: PassName | null;
}> = ({ passState = { place: 0, format: 0 }, activePass = null }) => {
  const passes: PassName[] = ["place", "format"];

  return (
    <div className={styles.passIndicator}>
      {passes.map((pass, i) => {
        const isActive = activePass === pass;
        const isComplete = passState[pass] >= 100;

        return (
          <React.Fragment key={pass}>
            {i > 0 && (
              <div className={`${styles.passConnector} ${passState[passes[i - 1]] >= 100 ? styles.active : ''}`} />
            )}
            <div
              className={`${styles.passDot} ${isActive ? styles.active : ''} ${isComplete ? styles.complete : ''}`}
            >
              {i + 1}
            </div>
          </React.Fragment>
        );
      })}
    </div>
  );
};

export default PassIndicator;
