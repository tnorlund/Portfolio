import React, { useEffect, useState, Fragment } from "react";

import { api } from "../../../services/api";
import {
  ImageCountApiResponse,
  LabelValidationCountResponse,
} from "../../../types/api";

const STATUS_COLORS: Record<string, string> = {
  VALID: "var(--color-green)",
  INVALID: "var(--color-red)",
  PENDING: "var(--color-blue)",
  NEEDS_REVIEW: "var(--color-yellow)",
  NONE: "var(--text-color)",
};

export default function LabelValidationChart() {
  const [counts, setCounts] = useState<LabelValidationCountResponse | null>(
    null
  );

  useEffect(() => {
    api.fetchLabelValidationCount().then((fetchedCount) => {
      setCounts(fetchedCount);
    });
  }, []);

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

  const statuses = ["VALID", "INVALID", "PENDING", "NEEDS_REVIEW", "NONE"];
  const rows = Object.entries(counts);

  const totalsByLabel = rows.map(([, data]) =>
    statuses.reduce((sum, s) => sum + (data[s] || 0), 0)
  );
  const maxTotal = Math.max(...totalsByLabel);

  return (
    <div
      style={{
        width: "100%",
        maxWidth: "900px",
        margin: "2rem auto",
        fontFamily: "sans-serif",
      }}
    >
      {/* title */}
      <div style={{ display: "flex", justifyContent: "center" }}>
        <h2 style={{ margin: 0 }}>Label Validation Count</h2>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "max-content auto max-content",
          columnGap: "8px",
          rowGap: "6px",
          alignItems: "center",
        }}
      >
        {rows.map(([label, data]) => {
          const total = statuses.reduce((sum, s) => sum + (data[s] || 0), 0);
          const rowWidthPercent = (total / maxTotal) * 100;
          return (
            <Fragment key={label}>
              <div style={{ fontWeight: 500, textAlign: "right" }}>
                {label.toLowerCase().replace(/_/g, " ")}
              </div>
              <div
                style={{
                  width: `${rowWidthPercent}%`,
                  display: "flex",
                  height: 20,
                  borderRadius: 6,
                  overflow: "hidden",
                }}
              >
                {statuses.map((status) => {
                  const count = data[status] || 0;
                  const widthPercent = total ? (count / total) * 100 : 0;
                  return (
                    <div
                      key={status}
                      style={{
                        width: `${widthPercent}%`,
                        backgroundColor: STATUS_COLORS[status],
                        height: "100%",
                      }}
                    />
                  );
                })}
              </div>
              <div style={{ textAlign: "right" }}>{total.toLocaleString()}</div>
            </Fragment>
          );
        })}
      </div>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          // rowGap: "2px",
          columnGap: "24px",
          marginBottom: "1rem",
          //   fontSize: "0.9rem",
          justifyContent: "center",
        }}
      >
        {statuses.map((status) => (
          <div
            key={status}
            style={{
              marginTop: "1rem",
              display: "flex",
              alignItems: "center",
              gap: "6px",
            }}
          >
            <span
              style={{
                width: "12px",
                height: "12px",
                backgroundColor: STATUS_COLORS[status],
                display: "inline-block",
                borderRadius: "2px",
              }}
            />
            <span>
              {status
                .toLowerCase()
                .replace(/_/g, " ")
                .split(" ")
                .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
                .join(" ")}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
