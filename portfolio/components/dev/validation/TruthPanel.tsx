import React, { useState } from "react";
import styles from "./Validation.module.css";
import { buildTruthChain, failureHint } from "./truthChain";
import { ReviewVerdict, ValidationReceipt } from "./types";

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

interface TruthPanelProps {
  receipt: ValidationReceipt;
  onHoverItem: (lineIds: number[] | null) => void;
  onReview: (verdict: ReviewVerdict, note: string) => void;
  saving: boolean;
}

export const TruthPanel: React.FC<TruthPanelProps> = ({
  receipt,
  onHoverItem,
  onReview,
  saving,
}) => {
  const [note, setNote] = useState("");
  const chain = buildTruthChain(receipt.items_sum, receipt.summary);
  const hint = failureHint(receipt);
  const summary = receipt.summary;
  const status = receipt.reconciliation_status ?? "no-baseline";

  const submit = (verdict: ReviewVerdict) => {
    onReview(verdict, note);
    setNote("");
  };

  return (
    <section className={styles.truthPanel} data-testid="truth-panel">
      <header className={styles.truthHeader}>
        <div>
          <span className={styles.eyebrow}>Truth chain</span>
          <strong>{receipt.merchant_name}</strong>
        </div>
        <span className={styles.statusBadge} data-status={status}>
          {status}
        </span>
      </header>

      <div className={styles.tenderRow} data-testid="tender-badges">
        <span className={styles.tenderBadge}>
          {summary?.tender_class ?? "tender ?"}
        </span>
        {summary?.card_network ? (
          <span className={styles.tenderBadge}>{summary.card_network}</span>
        ) : null}
        {summary?.card_last4 ? (
          <span className={styles.tenderBadge}>••{summary.card_last4}</span>
        ) : null}
        {summary?.ledger ? (
          <span className={styles.tenderBadge}>{summary.ledger}</span>
        ) : null}
        {summary?.bank_match_confidence !== null &&
        summary?.bank_match_confidence !== undefined ? (
          <span className={styles.tenderBadge}>
            conf {summary.bank_match_confidence.toFixed(2)}
          </span>
        ) : null}
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
            <tr>
              <td colSpan={4}>No line items extracted.</td>
            </tr>
          ) : null}
        </tbody>
      </table>

      <div className={styles.chain} data-testid="truth-chain">
        {chain.map((row) => (
          <div
            key={row.key}
            className={styles.chainRow}
            data-testid={`truth-row-${row.key}`}
            data-agreement={row.agreement}
            title={`vs ${row.referenceLabel}: ${money(row.reference)}`}
          >
            <span className={styles.chainLabel}>{row.label}</span>
            <span className={styles.chainValue}>{money(row.value)}</span>
            <span className={styles.chainDelta}>{signedMoney(row.delta)}</span>
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
          <strong>{hint.label}</strong>
          <span>{hint.detail}</span>
        </div>
      ) : null}

      <div className={styles.reviewBox}>
        <textarea
          className={styles.noteInput}
          value={note}
          placeholder="Note for Claude (what's wrong, what to repair)…"
          aria-label="Review note"
          onChange={(event) => setNote(event.target.value)}
          rows={2}
        />
        <div className={styles.reviewButtons}>
          <button
            type="button"
            className={styles.confirmButton}
            disabled={saving}
            onClick={() => submit("confirm")}
          >
            Confirm
          </button>
          <button
            type="button"
            className={styles.flagButton}
            disabled={saving}
            onClick={() => submit("flag")}
          >
            Flag
          </button>
        </div>
      </div>

      {receipt.reviews.length > 0 ? (
        <ul className={styles.reviewLog} data-testid="prior-reviews">
          {receipt.reviews.map((entry) => (
            <li key={`${entry.ts}-${entry.verdict}`}>
              <span data-status={entry.verdict}>{entry.verdict}</span>
              <span>{entry.note || "—"}</span>
              <small>{entry.ts.slice(0, 16).replace("T", " ")}</small>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
};

export default TruthPanel;
