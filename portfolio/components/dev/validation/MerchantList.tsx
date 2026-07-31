import React, { useDeferredValue, useMemo, useState } from "react";
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

export type MerchantSort = "mismatch" | "match-rate" | "name";

interface MerchantListProps {
  merchants: MerchantRow[];
  totals: Record<string, number>;
  receipts: number;
  selected: string | null;
  statusFilter: StatusFilter;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  onSelect: (merchant: string | null) => void;
  onStatusChange: (status: StatusFilter) => void;
}

const MerchantList = React.forwardRef<HTMLInputElement, MerchantListProps>(
  function MerchantList(
    {
      merchants,
      totals,
      receipts,
      selected,
      statusFilter,
      loading = false,
      error = null,
      onRetry,
      onSelect,
      onStatusChange,
    },
    searchRef,
  ) {
    const [query, setQuery] = useState("");
    const [sort, setSort] = useState<MerchantSort>("mismatch");
    const deferredQuery = useDeferredValue(query.trim().toLocaleLowerCase());

    const visibleMerchants = useMemo(() => {
      const filtered = deferredQuery
        ? merchants.filter((merchant) =>
            merchant.name.toLocaleLowerCase().includes(deferredQuery),
          )
        : merchants;
      return [...filtered].sort((left, right) => {
        if (sort === "name") return left.name.localeCompare(right.name);
        if (sort === "match-rate") {
          return (
            left.match_rate - right.match_rate ||
            right.mismatch - left.mismatch ||
            left.name.localeCompare(right.name)
          );
        }
        return (
          right.mismatch - left.mismatch ||
          left.match_rate - right.match_rate ||
          left.name.localeCompare(right.name)
        );
      });
    }, [deferredQuery, merchants, sort]);

    return (
      <aside className={styles.merchantPanel} data-testid="merchant-panel">
        <div className={styles.panelHead}>
          <div>
            <span className={styles.eyebrow}>Merchants</span>
            <strong>{merchants.length || "—"}</strong>
          </div>
          <small>
            {receipts} receipts · {totals.mismatch ?? 0} mismatch ·{" "}
            {totals.near ?? 0} near
          </small>
        </div>

        <div className={styles.merchantTools}>
          <label className={styles.searchField}>
            <span aria-hidden="true">⌕</span>
            <input
              ref={searchRef}
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search merchants"
              aria-label="Search merchants"
            />
            <kbd>M</kbd>
          </label>
          <label className={styles.sortField}>
            <span>Sort</span>
            <select
              value={sort}
              aria-label="Sort merchants"
              onChange={(event) => setSort(event.target.value as MerchantSort)}
            >
              <option value="mismatch">Mismatch count</option>
              <option value="match-rate">Match rate</option>
              <option value="name">Name</option>
            </select>
          </label>
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

        {error ? (
          <div className={styles.compactState} role="alert">
            <span>{error}</span>
            {onRetry ? (
              <button type="button" onClick={onRetry}>
                Retry
              </button>
            ) : null}
          </div>
        ) : null}

        <ul className={styles.merchantList} aria-busy={loading}>
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
          {visibleMerchants.map((merchant) => {
            const rate = Math.round(merchant.match_rate * 100);
            return (
              <li key={merchant.name}>
                <button
                  type="button"
                  className={styles.merchantButton}
                  data-active={merchant.name === selected}
                  onClick={() => onSelect(merchant.name)}
                  title={`${merchant.match} match · ${merchant.near} near · ${merchant.mismatch} mismatch · ${merchant["no-baseline"]} no-baseline`}
                >
                  <span className={styles.merchantName}>{merchant.name}</span>
                  <span className={styles.merchantMetrics}>
                    <span>{merchant.mismatch} off</span>
                    <span>{rate}%</span>
                  </span>
                  <span
                    className={styles.rateBar}
                    role="meter"
                    aria-label={`${merchant.name} match rate`}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={rate}
                  >
                    <span
                      className={styles.rateFill}
                      style={{ width: `${rate}%` }}
                    />
                  </span>
                </button>
              </li>
            );
          })}
        </ul>

        {loading ? (
          <div className={styles.inlineLoading}>Loading merchants…</div>
        ) : visibleMerchants.length === 0 && query ? (
          <div className={styles.inlineLoading}>No merchant matches “{query}”.</div>
        ) : null}
      </aside>
    );
  },
);

export default MerchantList;
