import { useEffect, useState } from "react";
import { fetchLabelValidationCount } from "../api";
import { LabelValidationCountResponse } from "../interfaces";

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
    fetchLabelValidationCount().then(setCounts);
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
        margin: "0 auto",
        fontFamily: "sans-serif",
      }}
    >
      {/* title */}
      <div style={{ display: "flex", justifyContent: "center" }}>
        <h2>Label Validation Count</h2>
      </div>
      {rows.map(([label, data]) => {
        const total = statuses.reduce((sum, s) => sum + (data[s] || 0), 0);
        return (
          <div
            key={label}
            style={{
              display: "flex",
              alignItems: "center",
              marginBottom: "6px",
            }}
          >
            <div
              style={{
                width: 160,
                textAlign: "right",
                marginRight: 16,
                fontWeight: 500,
              }}
            >
              {label.toLowerCase().replace(/_/g, " ")}
            </div>
            <div style={{ flex: 1 }}>
              <div
                style={{
                  width: `${(total / maxTotal) * 100}%`,
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
            </div>
          </div>
        );
      })}
      <div
        style={{
          display: "flex",
          gap: "16px",
          marginBottom: "1rem",
          //   fontSize: "0.9rem",
          justifyContent: "center",
        }}
      >
        {statuses.map((status) => (
          <div
            key={status}
            style={{
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
