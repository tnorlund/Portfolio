// DEV-ONLY validation workstation. Reviews line-item extraction one merchant
// at a time against the printed + bank truth chain, and writes verdicts to the
// review log that Claude reads back. Data comes from the local shim
// (portfolio/dev-harness/validation_shim.py) via the /api/validation rewrite,
// which only exists in PHASE_DEVELOPMENT_SERVER.
import Head from "next/head";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchMerchants,
  fetchReceipt,
  fetchWorklist,
  postReview,
} from "../../components/dev/validation/client";
import MerchantList from "../../components/dev/validation/MerchantList";
import ReceiptCanvas from "../../components/dev/validation/ReceiptCanvas";
import TruthPanel from "../../components/dev/validation/TruthPanel";
import {
  MerchantsResponse,
  ReviewVerdict,
  StatusFilter,
  ValidationReceipt,
  WorklistRow,
} from "../../components/dev/validation/types";
import styles from "../../components/dev/validation/Validation.module.css";
import { useImageFormatSupport } from "../../components/ui/Figures/ReceiptFlow/useImageFormatSupport";

export default function ValidationWorkstation() {
  const formatSupport = useImageFormatSupport();
  const [index, setIndex] = useState<MerchantsResponse | null>(null);
  const [merchant, setMerchant] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("failures");
  const [worklist, setWorklist] = useState<WorklistRow[]>([]);
  const [position, setPosition] = useState(0);
  const [receipt, setReceipt] = useState<ValidationReceipt | null>(null);
  const [highlight, setHighlight] = useState<number[] | null>(null);
  const [showSections, setShowSections] = useState(true);
  const [showItems, setShowItems] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchMerchants()
      .then(setIndex)
      .catch((cause) => setError(String(cause)));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setReceipt(null);
    fetchWorklist(merchant, statusFilter)
      .then((response) => {
        if (cancelled) return;
        setWorklist(response.receipts);
        setPosition(0);
        setError(null);
      })
      .catch((cause) => !cancelled && setError(String(cause)));
    return () => {
      cancelled = true;
    };
  }, [merchant, statusFilter]);

  const current = worklist[position] ?? null;

  useEffect(() => {
    if (!current) {
      setReceipt(null);
      return;
    }
    let cancelled = false;
    setHighlight(null);
    fetchReceipt(current.image_id, current.receipt_id)
      .then((detail) => !cancelled && setReceipt(detail))
      .catch((cause) => !cancelled && setError(String(cause)));
    return () => {
      cancelled = true;
    };
  }, [current]);

  const step = useCallback(
    (delta: number) =>
      setPosition((value) =>
        Math.max(0, Math.min(worklist.length - 1, value + delta)),
      ),
    [worklist.length],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA)$/.test(target.tagName)) return;
      if (event.key === "ArrowRight" || event.key === "j") step(1);
      if (event.key === "ArrowLeft" || event.key === "k") step(-1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step]);

  const onReview = useCallback(
    async (verdict: ReviewVerdict, note: string) => {
      if (!receipt || !current) return;
      setSaving(true);
      try {
        const entry = await postReview({
          image_id: receipt.image_id,
          receipt_id: receipt.receipt_id,
          verdict,
          note,
          merchant: receipt.merchant_name,
          status: receipt.reconciliation_status ?? "no-baseline",
          delta: receipt.delta,
        });
        setReceipt((value) =>
          value ? { ...value, reviews: [...value.reviews, entry] } : value,
        );
        setError(null);
        step(1);
      } catch (cause) {
        setError(String(cause));
      } finally {
        setSaving(false);
      }
    },
    [receipt, current, step],
  );

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
          merchants={index?.merchants ?? []}
          totals={index?.totals ?? {}}
          receipts={index?.receipts ?? 0}
          selected={merchant}
          statusFilter={statusFilter}
          onSelect={setMerchant}
          onStatusChange={setStatusFilter}
        />

        <main className={styles.centerPanel}>
          {error ? <div className={styles.error}>{error}</div> : null}
          <div className={styles.rotationBar}>
            <button
              type="button"
              onClick={() => step(-1)}
              disabled={position === 0}
            >
              ← prev
            </button>
            <button
              type="button"
              onClick={() => step(1)}
              disabled={position >= worklist.length - 1}
            >
              next →
            </button>
            <span className={styles.position}>
              {worklist.length === 0 ? 0 : position + 1} / {worklist.length}
            </span>
            <strong>{heading}</strong>
            <span className={styles.toggles}>
              <label>
                <input
                  type="checkbox"
                  checked={showSections}
                  onChange={(event) => setShowSections(event.target.checked)}
                />{" "}
                sections
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={showItems}
                  onChange={(event) => setShowItems(event.target.checked)}
                />{" "}
                items
              </label>
            </span>
          </div>

          {receipt ? (
            <ReceiptCanvas
              receipt={receipt}
              formatSupport={formatSupport}
              highlightLineIds={highlight}
              showSections={showSections}
              showItems={showItems}
            />
          ) : (
            <div className={styles.empty}>
              {worklist.length === 0 ? "Nothing queued." : "Loading receipt…"}
            </div>
          )}
        </main>

        {receipt ? (
          <TruthPanel
            receipt={receipt}
            onHoverItem={setHighlight}
            onReview={onReview}
            saving={saving}
          />
        ) : (
          <section className={styles.truthPanel} />
        )}
      </div>
    </>
  );
}
