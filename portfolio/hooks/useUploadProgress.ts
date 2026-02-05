import { useCallback, useEffect, useRef, useState } from "react";
import type { UploadReceiptProgress, UploadStatusResponse } from "../types/api";

export type UploadStage =
  | "pending"
  | "uploading"
  | "uploaded"
  | "ocr"
  | "processing"
  | "complete"
  | "failed";

export interface FileUploadState {
  file: File;
  stage: UploadStage;
  uploadPercent: number;
  imageId: string | null;
  jobId: string | null;
  receiptCount: number;
  receipts: UploadReceiptProgress[];
  error: string | null;
}

const MAX_CONCURRENT = 3;
const POLL_INTERVAL_MS = 3000;

export function useUploadProgress(apiUrl: string) {
  const [fileStates, setFileStates] = useState<FileUploadState[]>([]);
  const pollTimers = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map());
  const activeUploads = useRef(0);
  const uploadQueue = useRef<number[]>([]);
  const mountedRef = useRef(true);

  // Cleanup on unmount
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      pollTimers.current.forEach((timer) => {
        clearInterval(timer);
      });
      pollTimers.current.clear();
    };
  }, []);

  const updateFile = useCallback((index: number, updates: Partial<FileUploadState>) => {
    if (!mountedRef.current) return;
    setFileStates((prev) => {
      const next = [...prev];
      if (index < next.length) {
        next[index] = { ...next[index], ...updates };
      }
      return next;
    });
  }, []);

  const pollStatus = useCallback(
    (imageId: string, fileIndex: number) => {
      if (pollTimers.current.has(imageId)) return;

      const timer = setInterval(async () => {
        if (!mountedRef.current) {
          clearInterval(timer);
          pollTimers.current.delete(imageId);
          return;
        }

        try {
          const res = await fetch(
            `${apiUrl}/upload-status?image_id=${encodeURIComponent(imageId)}`,
          );
          if (!res.ok) return;

          const data: UploadStatusResponse = await res.json();

          // Derive stage from status
          let stage: UploadStage = "ocr";
          if (data.ocr_status === "FAILED") {
            stage = "failed";
          } else if (data.ocr_status === "COMPLETED") {
            if (data.receipt_count === 0) {
              // NATIVE image or no receipts — done
              stage = "complete";
            } else {
              const allMerchant = data.receipts.every((r) => r.merchant_found);
              const allLabelsValidated = data.receipts.every(
                (r) => r.total_labels > 0 && r.validated_labels >= r.total_labels,
              );
              if (allMerchant && allLabelsValidated) {
                stage = "complete";
              } else {
                stage = "processing";
              }
            }
          }

          updateFile(fileIndex, {
            stage,
            receiptCount: data.receipt_count,
            receipts: data.receipts,
          });

          // Stop polling when terminal
          if (stage === "complete" || stage === "failed") {
            clearInterval(timer);
            pollTimers.current.delete(imageId);
          }
        } catch {
          // Silently retry on next interval
        }
      }, POLL_INTERVAL_MS);

      pollTimers.current.set(imageId, timer);
    },
    [apiUrl, updateFile],
  );

  const processUpload = useCallback(
    async (fileIndex: number) => {
      if (!mountedRef.current) return;

      setFileStates((prev) => {
        const next = [...prev];
        if (fileIndex < next.length) {
          next[fileIndex] = { ...next[fileIndex], stage: "uploading" };
        }
        return next;
      });

      // Read current file from state
      let file: File | null = null;
      setFileStates((prev) => {
        file = prev[fileIndex]?.file ?? null;
        return prev;
      });
      if (!file) return;
      const currentFile: File = file;

      try {
        // 1. Get presigned URL
        const presignRes = await fetch(`${apiUrl}/upload-receipt`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            filename: currentFile.name,
            content_type: currentFile.type,
          }),
        });

        if (!presignRes.ok) {
          throw new Error(`Failed to request upload URL (status ${presignRes.status})`);
        }

        const { upload_url, image_id, job_id } = await presignRes.json();
        updateFile(fileIndex, { imageId: image_id, jobId: job_id });

        // 2. Upload via XHR for progress tracking
        await new Promise<void>((resolve, reject) => {
          const xhr = new XMLHttpRequest();
          xhr.open("PUT", upload_url);
          xhr.setRequestHeader("Content-Type", currentFile.type);

          xhr.upload.onprogress = (e) => {
            if (e.lengthComputable && mountedRef.current) {
              const percent = Math.round((e.loaded / e.total) * 100);
              updateFile(fileIndex, { uploadPercent: percent });
            }
          };

          xhr.onload = () => {
            if (xhr.status >= 200 && xhr.status < 300) {
              resolve();
            } else {
              reject(new Error(`Upload failed (status ${xhr.status})`));
            }
          };

          xhr.onerror = () => reject(new Error("Upload network error"));
          xhr.send(currentFile);
        });

        // 3. Upload complete, start polling
        updateFile(fileIndex, { stage: "ocr", uploadPercent: 100 });
        pollStatus(image_id, fileIndex);
      } catch (err) {
        const errorMsg = err instanceof Error ? err.message : "Upload failed";
        updateFile(fileIndex, { stage: "failed", error: errorMsg });
      } finally {
        activeUploads.current--;
        drainQueue();
      }
    },
    [apiUrl, updateFile, pollStatus],
  );

  const drainQueue = useCallback(() => {
    while (activeUploads.current < MAX_CONCURRENT && uploadQueue.current.length > 0) {
      const nextIndex = uploadQueue.current.shift()!;
      activeUploads.current++;
      processUpload(nextIndex);
    }
  }, [processUpload]);

  const addFiles = useCallback(
    (newFiles: File[]) => {
      setFileStates((prev) => {
        const startIndex = prev.length;
        const newStates: FileUploadState[] = newFiles.map((file) => ({
          file,
          stage: "pending" as UploadStage,
          uploadPercent: 0,
          imageId: null,
          jobId: null,
          receiptCount: 0,
          receipts: [],
          error: null,
        }));

        // Queue new files for upload
        for (let i = 0; i < newFiles.length; i++) {
          uploadQueue.current.push(startIndex + i);
        }

        // Kick off queue processing after state update
        setTimeout(() => drainQueue(), 0);

        return [...prev, ...newStates];
      });
    },
    [drainQueue],
  );

  const clearCompleted = useCallback(() => {
    setFileStates((prev) => prev.filter((f) => f.stage !== "complete" && f.stage !== "failed"));
  }, []);

  const clearAll = useCallback(() => {
    // Stop all polling
    pollTimers.current.forEach((timer) => {
      clearInterval(timer);
    });
    pollTimers.current.clear();
    setFileStates([]);
  }, []);

  return { fileStates, addFiles, clearCompleted, clearAll };
}
