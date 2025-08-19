import React, { useEffect, useState, useRef } from "react";

import { api } from "../../../services/api";
import { MerchantCountsResponse } from "../../../types/api";

export default function MerchantCount() {
  const [counts, setCounts] = useState<MerchantCountsResponse | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [rowHeight, setRowHeight] = useState<number | null>(null);

  useEffect(() => {
    api
      .fetchMerchantCounts()
      .then(setCounts)
      .catch((err) => console.error("Failed to fetch merchant counts:", err));
  }, []);

  const flattened: [string, number][] = counts
    ? counts.flatMap((obj) => Object.entries(obj))
    : [];
  const topFive = flattened.sort(([, a], [, b]) => b - a).slice(0, 5);
  const maxCount = topFive.length ? topFive[0][1] : 0;

  useEffect(() => {
    if (counts && !rowHeight && containerRef.current) {
      const totalHeight = containerRef.current.clientHeight;
      setRowHeight(totalHeight / topFive.length);
    }
  }, [counts, rowHeight, topFive.length]);

  if (!counts) {
    return (
      <div
        style={{
          minHeight: "675px",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <p>Loading...</p>
      </div>
    );
  }

  return (
    <div
      style={{
        width: "100%",
        maxWidth: "900px",
        margin: "2rem auto",
        fontFamily: "sans-serif",
      }}
    >
      <div style={{ display: "flex", justifyContent: "center" }}>
        <h2 style={{ margin: 0 }}>Top 5 Merchants</h2>
      </div>
      <div
        ref={containerRef}
        style={{
          display: "grid",
          gridTemplateColumns: "max-content auto max-content",
          //   gridTemplateColumns: "max-content 1fr max-content",
          columnGap: "8px",
          rowGap: "6px",
          alignItems: "center",
          //   justifyItems: "start",
        }}
      >
        {topFive.map(([merchant, count]) => (
          <React.Fragment key={merchant}>
            <div style={{ fontWeight: 500, justifySelf: "end" }}>
              {merchant}
            </div>
            <div>
              <div
                style={{
                  width: `${(count / maxCount) * 100}%`,
                  height: 20,
                  borderRadius: 6,
                  backgroundColor: "var(--text-color)",
                }}
              />
            </div>
            <div style={{ textAlign: "right" }}>{count.toLocaleString()}</div>
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}
