// DEV-ONLY validation workstation. Reviews line-item extraction one merchant
// at a time against the printed + bank truth chain, and writes verdicts to the
// review log that Claude reads back. Data comes from the local shim
// (portfolio/dev-harness/validation_shim.py) via the /api/validation rewrite,
// which only exists in PHASE_DEVELOPMENT_SERVER.
import Head from "next/head";
import React, {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  fetchMerchants,
  fetchQueues,
  fetchReceipt,
  fetchReviews,
  fetchWorklist,
  postReview,
} from "../../components/dev/validation/client";
import MerchantList from "../../components/dev/validation/MerchantList";
import ReceiptCanvas from "../../components/dev/validation/ReceiptCanvas";
import ReviewLog from "../../components/dev/validation/ReviewLog";
import TruthPanel from "../../components/dev/validation/TruthPanel";
import { FAILURE_MODES, isGoldenReady } from "../../components/dev/validation/truthChain";
import {
  MerchantsResponse,
  OverlayMode,
  QueueSummary,
  ReviewEntry,
  ReviewExtras,
  ReviewVerdict,
  StatusFilter,
  ValidationReceipt,
  WorklistRow,
} from "../../components/dev/validation/types";
import styles from "../../components/dev/validation/Validation.module.css";
import { useImageFormatSupport } from "../../components/ui/Figures/ReceiptFlow/useImageFormatSupport";

const errorMessage = (cause: unknown): string =>
  cause instanceof Error ? cause.message : String(cause);

export default function ValidationWorkstation() {
  const formatSupport = useImageFormatSupport();
  const merchantSearchRef = useRef<HTMLInputElement>(null);
  const jumpTargetRef = useRef<Pick<ReviewEntry, "image_id" | "receipt_id"> | null>(
    null,
  );
  const [index, setIndex] = useState<MerchantsResponse | null>(null);
  const [merchant, setMerchant] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("failures");
  const [queues, setQueues] = useState<QueueSummary[]>([]);
  const [queue, setQueue] = useState<string | null>(null);
  const [worklist, setWorklist] = useState<WorklistRow[]>([]);
  const [position, setPosition] = useState(0);
  const [receipt, setReceipt] = useState<ValidationReceipt | null>(null);
  const [reviews, setReviews] = useState<ReviewEntry[]>([]);
  const [highlight, setHighlight] = useState<number[] | null>(null);
  const [overlayMode, setOverlayMode] = useState<OverlayMode>("both");
  const [flagDialogOpen, setFlagDialogOpen] = useState(false);
  const [flagNote, setFlagNote] = useState("");
  const [flagReason, setFlagReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [indexLoading, setIndexLoading] = useState(true);
  const [reviewsLoading, setReviewsLoading] = useState(true);
  const [worklistLoading, setWorklistLoading] = useState(true);
  const [receiptLoading, setReceiptLoading] = useState(false);
  const [indexError, setIndexError] = useState<string | null>(null);
  const [reviewsError, setReviewsError] = useState<string | null>(null);
  const [worklistError, setWorklistError] = useState<string | null>(null);
  const [receiptError, setReceiptError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [reloadVersion, setReloadVersion] = useState(0);
  const [queueVersion, setQueueVersion] = useState(0);
  const [receiptVersion, setReceiptVersion] = useState(0);

  const retryAll = useCallback(() => {
    setActionError(null);
    setReloadVersion((version) => version + 1);
    setQueueVersion((version) => version + 1);
    setReceiptVersion((version) => version + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setIndexLoading(true);
    setReviewsLoading(true);
    void Promise.allSettled([
      fetchMerchants(),
      fetchReviews(),
      fetchQueues(),
    ]).then(([merchantResult, reviewResult, queueResult]) => {
      if (cancelled) return;
      if (merchantResult.status === "fulfilled") {
        setIndex(merchantResult.value);
        setIndexError(null);
      } else {
        setIndexError(errorMessage(merchantResult.reason));
      }
      if (reviewResult.status === "fulfilled") {
        setReviews(reviewResult.value.entries);
        setReviewsError(null);
      } else {
        setReviewsError(errorMessage(reviewResult.reason));
      }
      // A shim without queue files is the normal case, so a failure here
      // leaves the filters working rather than blocking the page.
      setQueues(
        queueResult.status === "fulfilled" ? queueResult.value.queues : [],
      );
      setIndexLoading(false);
      setReviewsLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [reloadVersion]);

  useEffect(() => {
    let cancelled = false;
    setReceipt(null);
    setWorklistLoading(true);
    setWorklistError(null);
    void fetchWorklist(merchant, statusFilter, 1000, queue)
      .then((response) => {
        if (cancelled) return;
        setWorklist(response.receipts);
        // A stale queue entry is worth saying out loud, but it must not hide
        // the receipts the queue did resolve.
        if (response.missing && response.missing.length > 0) {
          setActionError(
            `${response.missing.length} queued receipt(s) are not in the index; ` +
              "the queue is stale or points at another table.",
          );
        }
        const target = jumpTargetRef.current;
        const targetPosition = target
          ? response.receipts.findIndex(
              (row) =>
                row.image_id === target.image_id &&
                row.receipt_id === target.receipt_id,
            )
          : -1;
        if (target && targetPosition < 0) {
          setWorklistError(
            `Receipt ${target.receipt_id} is not present in the current 1,000-row index.`,
          );
        }
        setPosition(targetPosition >= 0 ? targetPosition : 0);
        jumpTargetRef.current = null;
      })
      .catch((cause) => {
        if (cancelled) return;
        setWorklist([]);
        setPosition(0);
        setWorklistError(errorMessage(cause));
      })
      .finally(() => {
        if (!cancelled) setWorklistLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [merchant, queue, queueVersion, statusFilter]);

  const current = worklist[position] ?? null;
  const currentImageId = current?.image_id;
  const currentReceiptId = current?.receipt_id;

  useEffect(() => {
    if (!currentImageId || currentReceiptId === undefined) {
      setReceipt(null);
      setReceiptLoading(false);
      return;
    }
    let cancelled = false;
    setHighlight(null);
    setReceipt(null);
    setReceiptError(null);
    setReceiptLoading(true);
    void fetchReceipt(currentImageId, currentReceiptId)
      .then((detail) => {
        if (!cancelled) setReceipt(detail);
      })
      .catch((cause) => {
        if (!cancelled) setReceiptError(errorMessage(cause));
      })
      .finally(() => {
        if (!cancelled) setReceiptLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [currentImageId, currentReceiptId, receiptVersion]);

  const step = useCallback(
    (delta: number) =>
      setPosition((value) =>
        Math.max(0, Math.min(worklist.length - 1, value + delta)),
      ),
    [worklist.length],
  );

  const onReview = useCallback(
    async (
      verdict: ReviewVerdict,
      note: string,
      extras: ReviewExtras = {},
    ): Promise<boolean> => {
      if (!receipt || !current || saving) return false;
      setSaving(true);
      try {
        const entry = await postReview({
          image_id: receipt.image_id,
          receipt_id: receipt.receipt_id,
          verdict,
          note,
          reason: extras.reason ?? null,
          line_ids: extras.line_ids ?? [],
          merchant: receipt.merchant_name,
          status: receipt.reconciliation_status ?? "no-baseline",
          delta: receipt.delta,
        });
        setReceipt((value) =>
          value ? { ...value, reviews: [...value.reviews, entry] } : value,
        );
        setReviews((value) => [...value, entry]);
        setActionError(null);
        step(1);
        return true;
      } catch (cause) {
        setActionError(errorMessage(cause));
        return false;
      } finally {
        setSaving(false);
      }
    },
    [current, receipt, saving, step],
  );

  const openFlagDialog = useCallback(() => {
    if (!receipt || saving) return;
    setFlagNote("");
    // Pre-select the scout's diagnosis so agreeing is one click and
    // disagreeing is a deliberate change.
    setFlagReason(receipt.dossier?.failure_mode ?? "");
    setFlagDialogOpen(true);
  }, [receipt, saving]);

  const proposal = receipt?.dossier?.proposal ?? null;

  const approveFix = useCallback(() => {
    if (!receipt || !proposal || saving) return;
    const args = proposal.args?.line_ids;
    void onReview("approve-fix", "", {
      reason: receipt.dossier?.failure_mode ?? null,
      line_ids: Array.isArray(args)
        ? args.filter((value): value is number => typeof value === "number")
        : [],
    });
  }, [onReview, proposal, receipt, saving]);

  const promoteGolden = useCallback(() => {
    if (!receipt || saving || !isGoldenReady(receipt)) return;
    void onReview("golden", "");
  }, [onReview, receipt, saving]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const key = event.key.toLocaleLowerCase();
      if (key === "escape" && flagDialogOpen) {
        event.preventDefault();
        setFlagDialogOpen(false);
        return;
      }
      if (flagDialogOpen) return;

      const target = event.target as HTMLElement | null;
      const isTyping =
        Boolean(target?.isContentEditable) ||
        Boolean(target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));
      if (isTyping) return;

      if (key === "m") {
        event.preventDefault();
        merchantSearchRef.current?.focus();
      } else if (key === "j" || event.key === "ArrowRight") {
        event.preventDefault();
        step(1);
      } else if (key === "k" || event.key === "ArrowLeft") {
        event.preventDefault();
        step(-1);
      } else if (key === "c" && receipt && !saving) {
        event.preventDefault();
        void onReview("confirm", "");
      } else if (key === "f" && receipt && !saving) {
        event.preventDefault();
        openFlagDialog();
      } else if (key === "a" && receipt && !saving) {
        event.preventDefault();
        approveFix();
      } else if (key === "g" && receipt && !saving) {
        event.preventDefault();
        promoteGolden();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [
    approveFix,
    flagDialogOpen,
    onReview,
    openFlagDialog,
    promoteGolden,
    receipt,
    saving,
    step,
  ]);

  const jumpToReview = useCallback((entry: ReviewEntry) => {
    jumpTargetRef.current = entry;
    setMerchant(null);
    setQueue(null);
    setStatusFilter("all");
    setQueueVersion((version) => version + 1);
  }, []);

  const submitFlag = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (
      await onReview("flag", flagNote.trim(), {
        reason: flagReason || null,
      })
    ) {
      setFlagDialogOpen(false);
      setFlagNote("");
      setFlagReason("");
    }
  };

  const selectQueue = useCallback((name: string | null) => {
    setActionError(null);
    setQueue(name);
    if (name) setMerchant(null);
  }, []);

  const heading = useMemo(() => {
    if (!current) return "No receipts for this filter";
    return `${current.merchant} · receipt ${current.receipt_id}`;
  }, [current]);

  return (
    <>
      <Head>
        <title>Line-item validation — local review</title>
      </Head>
      <div className={styles.layout}>
        <MerchantList
          ref={merchantSearchRef}
          merchants={index?.merchants ?? []}
          totals={index?.totals ?? {}}
          receipts={index?.receipts ?? 0}
          selected={merchant}
          statusFilter={statusFilter}
          queues={queues}
          queue={queue}
          onQueueChange={selectQueue}
          loading={indexLoading}
          error={indexError}
          onRetry={retryAll}
          onSelect={setMerchant}
          onStatusChange={setStatusFilter}
        />

        <main className={styles.centerPanel}>
          {actionError ? (
            <div className={styles.error} role="alert">
              <span>{actionError}</span>
              <button type="button" onClick={() => setActionError(null)}>
                Dismiss
              </button>
            </div>
          ) : null}
          <div className={styles.rotationBar}>
            <button
              type="button"
              onClick={() => step(-1)}
              disabled={position === 0 || worklistLoading}
            >
              ← prev <kbd>K</kbd>
            </button>
            <button
              type="button"
              onClick={() => step(1)}
              disabled={position >= worklist.length - 1 || worklistLoading}
            >
              next <kbd>J</kbd> →
            </button>
            <span className={styles.position}>
              {worklist.length === 0 ? 0 : position + 1} / {worklist.length}
            </span>
            <strong className={styles.receiptHeading}>{heading}</strong>
            <div
              className={styles.overlayToggle}
              role="group"
              aria-label="Receipt overlay"
            >
              {(["sections", "items", "both"] as OverlayMode[]).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  data-active={overlayMode === mode}
                  onClick={() => setOverlayMode(mode)}
                >
                  {mode}
                </button>
              ))}
            </div>
          </div>

          {worklistLoading ? (
            <div className={styles.empty}>Loading review queue…</div>
          ) : worklistError ? (
            <div className={styles.resilientState} role="alert">
              <strong>Review queue unavailable</strong>
              <span>{worklistError}</span>
              <button type="button" onClick={retryAll}>
                Retry local shim
              </button>
            </div>
          ) : receiptLoading ? (
            <div className={styles.empty}>Loading receipt evidence…</div>
          ) : receiptError ? (
            <div className={styles.resilientState} role="alert">
              <strong>Receipt details unavailable</strong>
              <span>{receiptError}</span>
              <button type="button" onClick={() => setReceiptVersion((value) => value + 1)}>
                Retry receipt
              </button>
            </div>
          ) : receipt ? (
            <ReceiptCanvas
              receipt={receipt}
              formatSupport={formatSupport}
              highlightLineIds={highlight}
              overlayMode={overlayMode}
            />
          ) : (
            <div className={styles.empty}>Nothing queued for this filter.</div>
          )}
        </main>

        <div className={styles.rightRail}>
          {receipt ? (
            <TruthPanel
              receipt={receipt}
              onHoverItem={setHighlight}
              onReview={onReview}
              onFlagRequest={openFlagDialog}
              saving={saving}
            />
          ) : (
            <section className={styles.truthPanel}>
              <div className={styles.inlineLoading}>
                {receiptLoading
                  ? "Loading truth chain…"
                  : "Select a receipt to review its truth chain."}
              </div>
            </section>
          )}
          <ReviewLog
            entries={reviews}
            currentImageId={receipt?.image_id}
            currentReceiptId={receipt?.receipt_id}
            loading={reviewsLoading}
            error={reviewsError}
            onRetry={retryAll}
            onJump={jumpToReview}
          />
        </div>
      </div>

      {flagDialogOpen ? (
        <div
          className={styles.dialogBackdrop}
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setFlagDialogOpen(false);
          }}
        >
          <form
            className={styles.flagDialog}
            role="dialog"
            aria-modal="true"
            aria-labelledby="flag-dialog-title"
            onSubmit={submitFlag}
          >
            <div className={styles.dialogHeader}>
              <div>
                <span className={styles.eyebrow}>Flag receipt</span>
                <strong id="flag-dialog-title">Describe the failure mode</strong>
              </div>
              <button
                type="button"
                aria-label="Close flag dialog"
                onClick={() => setFlagDialogOpen(false)}
              >
                ×
              </button>
            </div>
            <label className={styles.reasonField}>
              <span>Failure mode</span>
              <select
                value={flagReason}
                aria-label="Failure mode"
                onChange={(event) => setFlagReason(event.target.value)}
              >
                <option value="">Unclassified</option>
                {FAILURE_MODES.map((mode) => (
                  <option key={mode.code} value={mode.code}>
                    {mode.label}
                  </option>
                ))}
              </select>
            </label>
            <textarea
              autoFocus
              className={styles.noteInput}
              value={flagNote}
              placeholder="What is wrong, and what should Claude repair?"
              aria-label="Review note"
              onChange={(event) => setFlagNote(event.target.value)}
              rows={4}
            />
            <div className={styles.dialogActions}>
              <small>Esc closes without saving</small>
              <button type="button" onClick={() => setFlagDialogOpen(false)}>
                Cancel
              </button>
              <button type="submit" className={styles.flagButton} disabled={saving}>
                {saving ? "Saving…" : "Save flag"}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </>
  );
}
