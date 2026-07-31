import React from "react";
import styles from "./Validation.module.css";
import { MerchantRow, StatusFilter } from "./types";

const FILTERS: StatusFilter[] = [
  "failures",
  "all",
  "mismatch",
  "near",
  "match",
  "no-baseline",
];

interface MerchantListProps {
  merchants: MerchantRow[];
  totals: Record<string, number>;
  receipts: number;
  selected: string | null;
  statusFilter: StatusFilter;
  onSelect: (merchant: string | null) => void;
  onStatusChange: (status: StatusFilter) => void;
}

export const MerchantList: React.FC<MerchantListProps> = ({
  merchants,
  totals,
  receipts,
  selected,
  statusFilter,
  onSelect,
  onStatusChange,
}) => (
  <aside className={styles.merchantPanel} data-testid="merchant-panel">
    <div className={styles.panelHead}>
      <span className={styles.eyebrow}>Merchants</span>
      <small>
        {receipts} receipts · {totals.mismatch ?? 0} mismatch ·{" "}
        {totals.near ?? 0} near
      </small>
    </div>

    <div className={styles.chips} role="group" aria-label="Status filter">
      {FILTERS.map((filter) => (
        <button
          key={filter}
          type="button"
          className={styles.chip}
          data-active={filter === statusFilter}
          onClick={() => onStatusChange(filter)}
        >
          {filter}
        </button>
      ))}
    </div>

    <ul className={styles.merchantList}>
      <li>
        <button
          type="button"
          className={styles.merchantButton}
          data-active={selected === null}
          onClick={() => onSelect(null)}
        >
          <span className={styles.merchantName}>All merchants</span>
          <span className={styles.merchantCount}>{receipts}</span>
        </button>
      </li>
      {merchants.map((merchant) => (
        <li key={merchant.name}>
          <button
            type="button"
            className={styles.merchantButton}
            data-active={merchant.name === selected}
            onClick={() => onSelect(merchant.name)}
            title={`${merchant.match} match · ${merchant.near} near · ${merchant.mismatch} mismatch · ${merchant["no-baseline"]} no-baseline`}
          >
            <span className={styles.merchantName}>{merchant.name}</span>
            <span className={styles.merchantCount}>{merchant.receipts}</span>
            <span className={styles.rateBar}>
              <span
                className={styles.rateFill}
                style={{ width: `${Math.round(merchant.match_rate * 100)}%` }}
              />
            </span>
          </button>
        </li>
      ))}
    </ul>
  </aside>
);

export default MerchantList;
