import type { FC } from "react";
import styles from "./LoadingSpinner.module.css";

interface LoadingSpinnerProps {
  size?: "small" | "medium" | "large";
}

export const LoadingSpinner: FC<LoadingSpinnerProps> = ({
  size = "medium",
}) => {
  return (
    <div className={`${styles.spinner} ${styles[size]}`}>
      <div className={styles.inner}></div>
    </div>
  );
};

export default LoadingSpinner;
