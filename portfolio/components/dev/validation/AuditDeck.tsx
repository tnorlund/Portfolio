// The blind audit deck. It samples the verdicts the agent applied without
// asking anyone, and shows the receipt with every one of the agent's
// conclusions withheld. The human commits first; only then is the agent's
// verdict revealed. A single disagreement freezes that failure class for
// the adjudicator and the writer — the deck is the loop's stop button.
import React, { useCallback, useEffect, useState } from "react";
import { fetchAuditDeck, fetchAuditReceipt, postAuditVerdict } from "./client";
import ReceiptCanvas from "./ReceiptCanvas";
import { buildTruthChain } from "./truthChain";
import {
  AuditReceipt,
  AuditRevealed,
  AuditSampleRef,
  ReviewVerdict,
} from "./types";
import styles from "./Validation.module.css";
import { useImageFormatSupport } from "../../ui/Figures/ReceiptFlow/useImageFormatSupport";

const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

const money = (value: number | null): string =>
  value === null || value === undefined ? "—" : currency.format(value);

const signedMoney = (value: number | null): string =>
  value === null || value === undefined
    ? "—"
    : `${value > 0 ? "+" : ""}${currency.format(value)}`;

const errorMessage = (cause: unknown): string =>
  cause instanceof Error ? cause.message : String(cause);

const evidenceText = (entry: unknown): string => {
  if (typeof entry === "string") return entry;
  if (entry && typeof entry === "object") {
    const row = entry as { label?: string; detail?: string; value?: unknown };
    return [row.label, row.detail ?? (row.value as string | undefined)]
      .filter(Boolean)
      .join(" — ");
  }
  return String(entry);
};

interface AuditDeckProps {
  onFrozen?: (classes: string[]) => void;
}

export const AuditDeck: React.FC<AuditDeckProps> = ({ onFrozen }) => {
  const formatSupport = useImageFormatSupport();
  const [passId, setPassId] = useState<string | null>(null);
  const [sample, setSample] = useState<AuditSampleRef[]>([]);
  const [totalAuto, setTotalAuto] = useState(0);
  const [position, setPosition] = useState(0);
  const [receipt, setReceipt] = useState<AuditReceipt | null>(null);
  const [revealed, setRevealed] = useState<AuditRevealed | null>(null);
  const [committed, setCommitted] = useState<ReviewVerdict | null>(null);
  const [freezeWritten, setFreezeWritten] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [deckLoading, setDeckLoading] = useState(true);
  const [receiptLoading, setReceiptLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setDeckLoading(true);
    void fetchAuditDeck()
      .then((response) => {
        if (cancelled) return;
        setPassId(response.pass_id);
        setSample(response.sample);
        setTotalAuto(response.total_auto);
        setError(response.error ?? null);
        onFrozen?.(response.frozen);
      })
      .catch((cause) => {
        if (!cancelled) setError(errorMessage(cause));
      })
      .finally(() => {
        if (!cancelled) setDeckLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [onFrozen, version]);

  const current = sample[position] ?? null;
  const currentImageId = current?.image_id;
  const currentReceiptId = current?.receipt_id;

  useEffect(() => {
    if (!currentImageId || currentReceiptId === undefined) {
      setReceipt(null);
      return;
    }
    let cancelled = false;
    setReceipt(null);
    setRevealed(null);
    setCommitted(null);
    setFreezeWritten([]);
    setNote("");
    setReceiptLoading(true);
    void fetchAuditReceipt(currentImageId, currentReceiptId)
      .then((detail) => {
        if (!cancelled) setReceipt(detail);
      })
      .catch((cause) => {
        if (!cancelled) setActionError(errorMessage(cause));
      })
      .finally(() => {
        if (!cancelled) setReceiptLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [currentImageId, currentReceiptId]);

  const commit = useCallback(
    async (verdict: "audit-agree" | "audit-disagree") => {
      if (!receipt || saving || committed) return;
      setSaving(true);
      setActionError(null);
      try {
        const response = await postAuditVerdict({
          image_id: receipt.image_id,
          receipt_id: receipt.receipt_id,
          verdict,
          note: note.trim(),
          merchant: receipt.merchant_name,
          status: receipt.reconciliation_status ?? "no-baseline",
          delta: receipt.delta,
          pass_id: passId,
        });
        setCommitted(verdict);
        setRevealed(response.revealed);
        setFreezeWritten(response.freeze_written ?? []);
        onFrozen?.(response.frozen);
        setSample((rows) =>
          rows.map((row, index) =>
            index === position ? { ...row, reviewed: true } : row,
          ),
        );
      } catch (cause) {
        setActionError(errorMessage(cause));
      } finally {
        setSaving(false);
      }
    },
    [committed, note, onFrozen, passId, position, receipt, saving],
  );

  const chain = receipt
    ? buildTruthChain(receipt.items_sum, receipt.summary)
    : [];
  const reviewedCount = sample.filter((row) => row.reviewed).length;

  return (
    <section className={styles.screenPanel} data-testid="audit-deck">
      <header className={styles.screenHead}>
        <div>
          <span className={styles.eyebrow}>Blind audit</span>
          <strong>
            {reviewedCount} / {sample.length} audited
          </strong>
        </div>
        <div className={styles.passPicker}>
          <small className={styles.screenMeta}>
            {passId ? `${passId} · ` : ""}
            {sample.length} sampled from {totalAuto} auto-applied
          </small>
          <button type="button" onClick={() => setVersion((v) => v + 1)}>
            Reload
          </button>
        </div>
      </header>

      {error ? (
        <div className={styles.resilientState} role="alert">
          <strong>Audit deck unavailable</strong>
          <span>{error}</span>
          <button type="button" onClick={() => setVersion((v) => v + 1)}>
            Retry local shim
          </button>
        </div>
      ) : null}
      {actionError ? (
        <div className={styles.error} role="alert">
          <span>{actionError}</span>
          <button type="button" onClick={() => setActionError(null)}>
            Dismiss
          </button>
        </div>
      ) : null}

      {deckLoading ? (
        <div className={styles.empty}>Drawing the blind sample…</div>
      ) : sample.length === 0 ? (
        <div className={styles.empty} data-testid="audit-empty">
          Nothing to audit: this pass applied no verdicts on its own.
        </div>
      ) : (
        <div className={styles.auditLayout}>
          <main className={styles.centerPanel}>
            <div className={styles.rotationBar}>
              <button
                type="button"
                onClick={() => setPosition((p) => Math.max(0, p - 1))}
                disabled={position === 0}
              >
                ← prev
              </button>
              <button
                type="button"
                onClick={() =>
                  setPosition((p) => Math.min(sample.length - 1, p + 1))
                }
                disabled={position >= sample.length - 1}
              >
                next →
              </button>
              <span className={styles.position}>
                {position + 1} / {sample.length}
              </span>
              <strong className={styles.receiptHeading}>
                {current?.merchant ?? "—"} · receipt {current?.receipt_id}
              </strong>
            </div>
            {receiptLoading ? (
              <div className={styles.empty}>Loading receipt evidence…</div>
            ) : receipt ? (
              <ReceiptCanvas
                receipt={receipt}
                formatSupport={formatSupport}
                highlightLineIds={null}
                overlayMode="both"
              />
            ) : (
              <div className={styles.empty}>No receipt loaded.</div>
            )}
          </main>

          <aside className={styles.truthPanel} data-testid="audit-panel">
            <div className={styles.blindNotice} data-testid="blind-notice">
              <strong>Blind review.</strong>{" "}
              The agent&rsquo;s diagnosis,
              confidence and proposed fix are hidden until you commit. Decide
              from the image and the figures alone.
            </div>

            <div className={styles.tableLabel}>
              <span className={styles.eyebrow}>Extracted items</span>
              <span>{receipt?.items.length ?? 0}</span>
            </div>
            <table className={styles.itemsTable} data-testid="audit-items">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Item</th>
                  <th>Price</th>
                </tr>
              </thead>
              <tbody>
                {(receipt?.items ?? []).map((item) => (
                  <tr key={`${item.item_index}-${item.name}`}>
                    <td>{item.item_index}</td>
                    <td>{item.name || <em>unnamed</em>}</td>
                    <td className={styles.numeric}>{money(item.price)}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className={styles.chain} data-testid="audit-chain">
              {chain.map((row) => (
                <div
                  key={row.key}
                  className={styles.chainRow}
                  data-agreement={row.agreement}
                >
                  <span className={styles.agreementMark} aria-hidden="true" />
                  <span className={styles.chainLabel}>{row.label}</span>
                  <span className={styles.chainValue}>{money(row.value)}</span>
                  <span className={styles.chainDelta}>
                    {signedMoney(row.delta)}
                  </span>
                </div>
              ))}
            </div>

            {receipt?.dossier && receipt.dossier.evidence.length > 0 ? (
              <ul
                className={styles.dossierEvidence}
                data-testid="audit-evidence"
              >
                {receipt.dossier.evidence.map((entry, index) => (
                  <li key={index}>{evidenceText(entry)}</li>
                ))}
              </ul>
            ) : null}

            {committed ? (
              <div
                className={styles.revealCard}
                data-testid="audit-reveal"
                data-verdict={committed}
              >
                <span className={styles.eyebrow}>
                  What the agent had concluded
                </span>
                <p>
                  <strong>
                    {revealed?.failure_mode ?? "no failure mode recorded"}
                  </strong>
                  {revealed?.verdict_recommendation
                    ? ` → ${revealed.verdict_recommendation}`
                    : null}
                  {revealed?.confidence
                    ? ` (${revealed.confidence} confidence)`
                    : null}
                  {revealed?.tier
                    ? ` · ${revealed.tier}${
                        revealed.reason ? ` (${revealed.reason})` : ""
                      }`
                    : null}
                </p>
                {revealed?.diagnosis ? <p>{revealed.diagnosis}</p> : null}
                {revealed?.signals_concurring?.length ? (
                  <p>Signals: {revealed.signals_concurring.join(", ")}</p>
                ) : null}
                {revealed?.proposal?.tool ? (
                  <code>{revealed.proposal.tool}</code>
                ) : null}
              </div>
            ) : (
              <>
                <textarea
                  className={styles.noteInput}
                  value={note}
                  rows={3}
                  aria-label="Audit note"
                  placeholder="Optional: what you saw that the agent may have missed"
                  onChange={(event) => setNote(event.target.value)}
                />
                <div className={styles.reviewButtons}>
                  <button
                    type="button"
                    className={styles.confirmButton}
                    disabled={saving || !receipt}
                    onClick={() => void commit("audit-agree")}
                  >
                    Agree with the agent
                  </button>
                  <button
                    type="button"
                    className={styles.flagButton}
                    disabled={saving || !receipt}
                    onClick={() => void commit("audit-disagree")}
                  >
                    Disagree — freeze this class
                  </button>
                </div>
              </>
            )}

            {freezeWritten.length > 0 ? (
              <div className={styles.frozenNotice} data-testid="freeze-written">
                <strong>Tier frozen: {freezeWritten.join(", ")}.</strong> The
                adjudicator demotes {freezeWritten.length === 1 ? "it" : "them"}{" "}
                to T2 on every later pass, and the writer applies nothing from{" "}
                {freezeWritten.length === 1 ? "it" : "them"}, until the
                marker{freezeWritten.length === 1 ? "" : "s"} in
                .dev-harness/freeze/ {freezeWritten.length === 1 ? "is" : "are"}{" "}
                cleared by hand.
              </div>
            ) : null}
          </aside>
        </div>
      )}
    </section>
  );
};

export default AuditDeck;
