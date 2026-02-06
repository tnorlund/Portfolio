import React from "react";
import type { FileUploadState, UploadStage } from "../../hooks/useUploadProgress";
import styles from "../../styles/Receipt.module.css";

interface UploadProgressPanelProps {
  fileStates: FileUploadState[];
  onClear: () => void;
}

function stageDotClass(stage: UploadStage): string {
  switch (stage) {
    case "pending":
      return styles.stageDotPending;
    case "uploading":
    case "uploaded":
    case "ocr":
      return styles.stageDotActive;
    case "processing":
      return styles.stageDotProcessing;
    case "complete":
      return styles.stageDotComplete;
    case "failed":
      return styles.stageDotFailed;
    default:
      return "";
  }
}

function processingStageLabel(processingStage: string | null): string {
  switch (processingStage) {
    case "DOWNLOADING":
      return "Downloading...";
    case "OCR_RUNNING":
      return "OCR Running...";
    case "UPLOADING_RESULTS":
      return "Uploading Results...";
    default:
      return "Waiting for worker...";
  }
}

function stageLabel(stage: UploadStage, processingStage: string | null): string {
  switch (stage) {
    case "pending":
      return "Waiting...";
    case "uploading":
      return "Uploading";
    case "uploaded":
      return "Uploaded";
    case "ocr":
      return processingStageLabel(processingStage);
    case "processing":
      return "Processing";
    case "complete":
      return "Complete";
    case "failed":
      return "Failed";
    default:
      return "";
  }
}

function FileCard({ state }: { state: FileUploadState }) {
  return (
    <div className={styles.uploadFileCard}>
      <div className={styles.uploadFileHeader}>
        <span className={`${styles.stageDot} ${stageDotClass(state.stage)}`} />
        <span className={styles.uploadFileName}>{state.file.name}</span>
        <span className={styles.uploadStageLabel}>{stageLabel(state.stage, state.processingStage)}</span>
      </div>

      {/* Progress bar during upload */}
      {state.stage === "uploading" && (
        <div className={styles.progressBarTrack}>
          <div
            className={styles.progressBarFill}
            style={{ width: `${state.uploadPercent}%` }}
            role="progressbar"
            aria-valuenow={state.uploadPercent}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`Uploading ${state.file.name}`}
          />
        </div>
      )}

      {/* Error message */}
      {state.error && <p className={styles.errorMessage}>{state.error}</p>}

      {/* Per-receipt progress */}
      {state.receipts.length > 0 && (
        <div className={styles.receiptRows}>
          {state.receipts.map((r) => (
            <div key={r.receipt_id} className={styles.receiptRow}>
              <span className={styles.receiptMerchant}>
                {r.merchant_found
                  ? r.merchant_name ?? "Unknown"
                  : "Finding merchant..."}
              </span>
              <span className={styles.receiptLabels}>
                {r.total_labels > 0
                  ? `${r.validated_labels}/${r.total_labels} labels`
                  : "Labeling..."}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function UploadProgressPanel({
  fileStates,
  onClear,
}: UploadProgressPanelProps) {
  if (fileStates.length === 0) return null;

  return (
    <div className={styles.uploadPanel}>
      <div className={styles.uploadPanelInner}>
        <div className={styles.uploadPanelHeader}>
          <h4 style={{ margin: 0 }}>Upload Progress</h4>
          <button className={styles.uploadClearBtn} onClick={onClear}>
            Clear
          </button>
        </div>
        <div className={styles.uploadPanelScroll}>
          {fileStates.map((state) => (
            <FileCard key={state.id} state={state} />
          ))}
        </div>
      </div>
    </div>
  );
}
