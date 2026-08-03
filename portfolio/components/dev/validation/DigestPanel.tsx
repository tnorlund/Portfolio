// The T1 batch digest: one row per merchant × failure-mode group the
// adjudicator wants applied. Approving a row is the sign-off the writer
// waits on, so the row has to carry enough evidence to say yes to a dozen
// receipts at once — count, net delta, and the exact tool that would run.
import React, { useCallback, useEffect, useState } from "react";
import { fetchDigest, postApprove } from "./client";
import { DigestGroup } from "./types";
import styles from "./Validation.module.css";

const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

const signedMoney = (value: number | null): string =>
  value === null || value === undefined
    ? "—"
    : `${value > 0 ? "+" : ""}${currency.format(value)}`;

const errorMessage = (cause: unknown): string =>
  cause instanceof Error ? cause.message : String(cause);

interface DigestPanelProps {
  /** Lifts the freeze state so the shell can warn on every screen. */
  onFrozen?: (classes: string[]) => void;
}

export const DigestPanel: React.FC<DigestPanelProps> = ({ onFrozen }) => {
  const [passId, setPassId] = useState<string | null>(null);
  const [passes, setPasses] = useState<string[]>([]);
  const [groups, setGroups] = useState<DigestGroup[]>([]);
  const [source, setSource] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [requested, setRequested] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void fetchDigest(requested)
      .then((response) => {
        if (cancelled) return;
        setPassId(response.pass_id);
        setPasses(response.passes);
        setGroups(response.groups);
        setSource(response.source);
        setWarning(response.warning ?? null);
        setError(response.error ?? null);
        onFrozen?.(response.frozen);
      })
      .catch((cause) => {
        if (!cancelled) setError(errorMessage(cause));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [onFrozen, requested, version]);

  const approve = useCallback(
    async (group: DigestGroup) => {
      if (pending) return;
      setPending(group.group_id);
      setActionError(null);
      try {
        await postApprove(passId, group.group_id);
        setGroups((rows) =>
          rows.map((row) =>
            row.group_id === group.group_id ? { ...row, approved: true } : row,
          ),
        );
      } catch (cause) {
        setActionError(errorMessage(cause));
      } finally {
        setPending(null);
      }
    },
    [passId, pending],
  );

  const outstanding = groups.filter((group) => !group.approved).length;

  return (
    <section className={styles.screenPanel} data-testid="digest-panel">
      <header className={styles.screenHead}>
        <div>
          <span className={styles.eyebrow}>Batch digest</span>
          <strong>
            {groups.length} group{groups.length === 1 ? "" : "s"} ·{" "}
            {outstanding} awaiting sign-off
          </strong>
        </div>
        <div className={styles.passPicker}>
          <label className={styles.queueField}>
            <span>Pass</span>
            <select
              value={passId ?? ""}
              aria-label="Adjudication pass"
              onChange={(event) => setRequested(event.target.value || null)}
            >
              {passId === null ? <option value="">— none —</option> : null}
              {passes.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <button type="button" onClick={() => setVersion((v) => v + 1)}>
            Reload
          </button>
        </div>
      </header>

      {source ? (
        <small className={styles.screenMeta} data-testid="digest-source">
          Read from {source}
        </small>
      ) : null}

      {warning ? (
        <div className={styles.compactState} role="alert">
          <span>{warning}</span>
        </div>
      ) : null}
      {error ? (
        <div className={styles.resilientState} role="alert">
          <strong>Digest unavailable</strong>
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

      {loading ? (
        <div className={styles.empty}>Loading adjudicated pass…</div>
      ) : groups.length === 0 ? (
        <div className={styles.empty} data-testid="digest-empty">
          No batch groups in this pass. The adjudicator writes them to
          .dev-harness/verdicts/ once it has run.
        </div>
      ) : (
        <ul className={styles.digestList}>
          {groups.map((group) => (
            <li
              key={group.group_id}
              className={styles.digestGroup}
              data-testid={`digest-group-${group.group_id}`}
              data-golden={group.golden_candidate}
              data-approved={group.approved}
              data-frozen={group.frozen}
            >
              <div className={styles.digestHead}>
                <div>
                  <strong className={styles.digestMerchant}>
                    {group.merchant}
                  </strong>
                  <span className={styles.dossierMode}>
                    {group.failure_mode}
                  </span>
                </div>
                <div className={styles.digestFigures}>
                  <span>
                    <small>Receipts</small>
                    <strong>{group.count}</strong>
                  </span>
                  <span>
                    <small>Net Δ</small>
                    <strong>{signedMoney(group.net_delta)}</strong>
                  </span>
                </div>
              </div>

              {group.golden_candidate ? (
                <p
                  className={styles.goldenWarning}
                  data-testid={`golden-warning-${group.group_id}`}
                >
                  <strong>Golden candidate.</strong> Approving promotes these
                  receipts into the fixture set and ratchets the CI floors —
                  every later run has to clear the bar this sets.
                </p>
              ) : null}

              {group.frozen ? (
                <p
                  className={styles.frozenNotice}
                  data-testid={`frozen-notice-${group.group_id}`}
                >
                  <strong>{group.failure_mode} is frozen.</strong> A blind audit
                  disagreed with this class; clear
                  .dev-harness/freeze/ before it can be approved.
                </p>
              ) : null}

              <div className={styles.digestAction}>
                <code>{group.action ?? "no action recorded"}</code>
              </div>

              <details className={styles.digestReceipts}>
                <summary>{group.receipts.length} receipt(s)</summary>
                <ul>
                  {group.receipts.map((row) => (
                    <li key={`${row.image_id}-${row.receipt_id}`}>
                      <code>{row.image_id.slice(0, 8)}</code> ·{" "}
                      {row.receipt_id}
                      <span className={styles.numeric}>
                        {signedMoney(row.delta)}
                      </span>
                    </li>
                  ))}
                </ul>
              </details>

              <div className={styles.digestButtons}>
                {group.approved ? (
                  <span
                    className={styles.approvedBadge}
                    data-testid={`approved-${group.group_id}`}
                  >
                    Approved — queued for the writer
                  </span>
                ) : (
                  <button
                    type="button"
                    className={
                      group.golden_candidate
                        ? styles.goldenButton
                        : styles.approveButton
                    }
                    disabled={group.frozen || pending === group.group_id}
                    onClick={() => void approve(group)}
                  >
                    {pending === group.group_id
                      ? "Approving…"
                      : `Approve ${group.count} receipt(s)`}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};

export default DigestPanel;
