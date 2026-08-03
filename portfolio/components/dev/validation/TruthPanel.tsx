import React from "react";
import styles from "./Validation.module.css";
import {
  Agreement,
  buildTruthChain,
  failureHint,
  isGoldenReady,
} from "./truthChain";
import {
  DossierEvidence,
  ReceiptDossier,
  ReviewExtras,
  ReviewVerdict,
  ValidationReceipt,
} from "./types";

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

const quantityLabel = (
  quantity: number | null,
  unitPrice: number | null,
): string | null => {
  if (quantity === null) return null;
  const amount = Number.isInteger(quantity)
    ? quantity.toFixed(0)
    : quantity.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
  return unitPrice === null ? `×${amount}` : `${amount} × ${money(unitPrice)}`;
};

const AGREEMENT_LABELS: Record<Agreement, string> = {
  agree: "Agrees",
  near: "Near",
  disagree: "Disagrees",
  unknown: "Missing",
};

const evidenceText = (entry: DossierEvidence): string => {
  if (typeof entry === "string") return entry;
  const detail = entry.detail ?? (entry.value === null ? null : entry.value);
  return [entry.label, detail === null || detail === undefined ? null : String(detail)]
    .filter((part): part is string => Boolean(part))
    .join(" — ");
};

/** Line ids the proposal would touch, when it names them. */
const proposalLineIds = (dossier: ReceiptDossier | null): number[] => {
  const raw = dossier?.proposal?.args?.line_ids;
  if (!Array.isArray(raw)) return [];
  return raw.filter((value): value is number => typeof value === "number");
};

const DossierCard: React.FC<{
  dossier: ReceiptDossier | null;
  error: string | null;
}> = ({ dossier, error }) => {
  if (error) {
    return (
      <div className={styles.dossier} data-testid="dossier-error" role="alert">
        <span className={styles.eyebrow}>Agent dossier</span>
        <p>Dossier could not be read: {error}</p>
      </div>
    );
  }
  if (!dossier) {
    return (
      <div className={styles.dossier} data-testid="dossier-empty" data-state="none">
        <span className={styles.eyebrow}>Agent dossier</span>
        <p>
          No dossier for this receipt. Review it on the harness&rsquo;s own
          evidence.
        </p>
      </div>
    );
  }

  const dryRun = dossier.proposal?.dry_run ?? null;
  return (
    <div className={styles.dossier} data-testid="dossier" data-state="present">
      <div className={styles.dossierHead}>
        <span className={styles.eyebrow}>Agent dossier</span>
        {dossier.failure_mode ? (
          <span className={styles.dossierMode} data-testid="dossier-mode">
            {dossier.failure_mode}
          </span>
        ) : null}
      </div>
      <p className={styles.dossierDiagnosis} data-testid="dossier-diagnosis">
        {dossier.diagnosis || "No diagnosis recorded."}
      </p>

      {dossier.evidence.length > 0 ? (
        <ul className={styles.dossierEvidence} data-testid="dossier-evidence">
          {dossier.evidence.map((entry, position) => (
            <li key={`${position}-${evidenceText(entry)}`}>
              {evidenceText(entry)}
            </li>
          ))}
        </ul>
      ) : null}

      {dossier.proposal ? (
        <div className={styles.dossierProposal} data-testid="dossier-proposal">
          <code>{dossier.proposal.tool}</code>
          {dryRun ? (
            <div className={styles.dossierDryRun} data-testid="dossier-dry-run">
              <span>
                Δ {signedMoney(dryRun.before_delta)} →{" "}
                <strong>{signedMoney(dryRun.after_delta)}</strong>
              </span>
              <span>
                {dryRun.before_status ?? "—"} →{" "}
                <strong>{dryRun.after_status ?? "—"}</strong>
              </span>
            </div>
          ) : (
            <span className={styles.dossierWarning} data-testid="dossier-no-dry-run">
              No dry run recorded — this proposal was never simulated.
            </span>
          )}
        </div>
      ) : (
        <div className={styles.dossierAbstain} data-testid="dossier-abstain">
          <strong>Abstained</strong>
          <span>
            {dossier.abstain_reason ||
              "No proposal, and no reason given for withholding one."}
          </span>
        </div>
      )}

      <small className={styles.dossierMeta}>
        {[dossier.source, dossier.author, dossier.generated_at]
          .filter((part): part is string => Boolean(part))
          .join(" · ")}
      </small>
    </div>
  );
};

interface TruthPanelProps {
  receipt: ValidationReceipt;
  onHoverItem: (lineIds: number[] | null) => void;
  onReview: (
    verdict: ReviewVerdict,
    note: string,
    extras?: ReviewExtras,
  ) => void;
  onFlagRequest: () => void;
  saving: boolean;
}

export const TruthPanel: React.FC<TruthPanelProps> = ({
  receipt,
  onHoverItem,
  onReview,
  onFlagRequest,
  saving,
}) => {
  const chain = buildTruthChain(receipt.items_sum, receipt.summary);
  const hint = failureHint(receipt);
  const summary = receipt.summary;
  const status = receipt.reconciliation_status ?? "no-baseline";
  const dossier = receipt.dossier;
  const goldenReady = isGoldenReady(receipt);
  const proposal = dossier?.proposal ?? null;
  const missingTargets = [
    receipt.image ? null : "image",
    receipt.sections.length > 0 ? null : "sections",
    receipt.items.length > 0 ? null : "items",
  ].filter((target): target is string => target !== null);

  return (
    <section className={styles.truthPanel} data-testid="truth-panel">
      <header className={styles.truthHeader}>
        <div>
          <span className={styles.eyebrow}>Truth chain</span>
          <strong>{receipt.merchant_name || "Unknown merchant"}</strong>
        </div>
        <span className={styles.statusBadge} data-status={status}>
          {status}
        </span>
      </header>

      {missingTargets.length > 0 ? (
        <div className={styles.reviewTarget} data-testid="review-target">
          <strong>Review target</strong>
          <span>Missing {missingTargets.join(" + ")} — inspect and flag the gap.</span>
        </div>
      ) : null}

      <div className={styles.tenderRow} data-testid="tender-badges">
        <span className={styles.tenderBadge} data-kind="tender">
          <small>Tender</small>
          <strong>{summary?.tender_class ?? "unknown"}</strong>
        </span>
        {summary?.card_network || summary?.card_last4 ? (
          <span className={styles.tenderBadge} data-kind="card">
            <small>Card</small>
            <strong>
              {[summary.card_network, summary.card_last4 ? `••${summary.card_last4}` : null]
                .filter(Boolean)
                .join(" ")}
            </strong>
          </span>
        ) : null}
        <span className={styles.tenderBadge} data-kind="ledger">
          <small>Ledger</small>
          <strong>{summary?.ledger ?? "unlinked"}</strong>
        </span>
        {summary?.bank_match_confidence !== null &&
        summary?.bank_match_confidence !== undefined ? (
          <span className={styles.tenderBadge} data-kind="confidence">
            <small>Bank match</small>
            <strong>{Math.round(summary.bank_match_confidence * 100)}%</strong>
          </span>
        ) : null}
      </div>

      <div className={styles.tableLabel}>
        <span className={styles.eyebrow}>Extracted items</span>
        <span>{receipt.items.length}</span>
      </div>
      <table className={styles.itemsTable} data-testid="items-table">
        <thead>
          <tr>
            <th>#</th>
            <th>Item</th>
            <th>Price</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {receipt.items.map((item) => {
            const quantity = quantityLabel(item.quantity, item.unit_price);
            return (
              <tr
                key={`${item.item_index}-${item.name}`}
                className={item.is_discount ? styles.discountRow : undefined}
                data-testid={`item-row-${item.item_index}`}
                onMouseEnter={() => onHoverItem(item.line_ids)}
                onMouseLeave={() => onHoverItem(null)}
              >
                <td>{item.item_index}</td>
                <td>
                  {item.name || <em>unnamed</em>}
                  {quantity ? (
                    <small className={styles.quantityChip}>{quantity}</small>
                  ) : null}
                </td>
                <td className={styles.numeric}>{money(item.price)}</td>
                <td>
                  <span
                    className={styles.miniStatus}
                    data-status={item.reconciliation_status ?? "no-baseline"}
                  >
                    {item.reconciliation_status ?? "—"}
                  </span>
                </td>
              </tr>
            );
          })}
          {receipt.items.length === 0 ? (
            <tr className={styles.missingTableRow}>
              <td colSpan={4}>
                <strong>No line items extracted.</strong>
                <span>This receipt stays in the queue as a repair target.</span>
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>

      <div className={styles.chainHeading}>
        <span className={styles.eyebrow}>Four-figure agreement</span>
        <small>each figure vs its upstream truth</small>
      </div>
      <div className={styles.chain} data-testid="truth-chain">
        {chain.map((row) => (
          <div
            key={row.key}
            className={styles.chainRow}
            data-testid={`truth-row-${row.key}`}
            data-agreement={row.agreement}
            title={`vs ${row.referenceLabel}: ${money(row.reference)}`}
          >
            <span className={styles.agreementMark} aria-hidden="true" />
            <span className={styles.chainLabel}>
              {row.label}
              <small>vs {row.referenceLabel}</small>
            </span>
            <span className={styles.chainValue}>{money(row.value)}</span>
            <span className={styles.chainDelta}>{signedMoney(row.delta)}</span>
            <span className={styles.agreementLabel}>
              {AGREEMENT_LABELS[row.agreement]}
            </span>
          </div>
        ))}
      </div>

      <div className={styles.deltaLine} data-testid="receipt-delta">
        <span>Δ vs baseline</span>
        <strong>{signedMoney(receipt.delta)}</strong>
      </div>

      {hint ? (
        <div
          className={styles.hint}
          data-testid="failure-hint"
          data-code={hint.code}
        >
          <span className={styles.hintChip}>{hint.label}</span>
          <span>{hint.detail}</span>
        </div>
      ) : null}

      {!summary ? (
        <div className={styles.missingSummary}>
          <strong>No receipt summary</strong>
          <span>Printed totals, tender, and bank truth are unavailable.</span>
        </div>
      ) : null}

      <DossierCard dossier={dossier} error={receipt.dossier_error} />

      <div className={styles.reviewButtons}>
        <button
          type="button"
          className={styles.confirmButton}
          disabled={saving}
          onClick={() => onReview("confirm", "")}
        >
          Confirm <kbd>C</kbd>
        </button>
        <button
          type="button"
          className={styles.flagButton}
          disabled={saving}
          onClick={onFlagRequest}
        >
          Flag with note <kbd>F</kbd>
        </button>
        <button
          type="button"
          className={styles.approveButton}
          disabled={saving || proposal === null}
          title={
            proposal
              ? `Queue ${proposal.tool} for the post-session writer`
              : "No proposed fix in the dossier to approve"
          }
          onClick={() =>
            onReview("approve-fix", "", {
              reason: dossier?.failure_mode ?? null,
              line_ids: proposalLineIds(dossier),
            })
          }
        >
          Approve fix <kbd>A</kbd>
        </button>
        <button
          type="button"
          className={styles.goldenButton}
          disabled={saving || !goldenReady}
          title={
            goldenReady
              ? "Promote into the bank-proven golden set"
              : "Golden needs a match receipt whose printed total the bank agrees with"
          }
          onClick={() => onReview("golden", "")}
        >
          Golden <kbd>G</kbd>
        </button>
      </div>
    </section>
  );
};

export default TruthPanel;
