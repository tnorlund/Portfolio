import { useState } from "react";
import { api } from "../api";
import type { CompileResult, ReviewResult } from "../types";

const SHIPPED = {
  wpc: 0.978,
  height: 1.0,
  density: 0.988,
  note: "1 blocker / 8 minors",
};

function ratioClass(v: number): string {
  if (v <= 1.08) return "good";
  if (v <= 1.18) return "warn";
  return "bad";
}

export function Review() {
  const [compileRes, setCompileRes] = useState<CompileResult | null>(null);
  const [reviewRes, setReviewRes] = useState<ReviewResult | null>(null);
  const [compiling, setCompiling] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const runCompile = async () => {
    setCompiling(true);
    setErr(null);
    try {
      setCompileRes(await api.compile());
    } catch (e) {
      setErr(String((e as Error).message));
    } finally {
      setCompiling(false);
    }
  };

  const runReview = async () => {
    setReviewing(true);
    setErr(null);
    try {
      setReviewRes(await api.review());
    } catch (e) {
      setErr(String((e as Error).message));
    } finally {
      setReviewing(false);
    }
  };

  const s = reviewRes?.summary;
  const coverage = compileRes ? Object.keys(compileRes.glyphs).length : 0;

  return (
    <div className="review-tab">
      <div className="review-actions">
        <button className="mini" onClick={() => void runCompile()} disabled={compiling}>
          {compiling ? "Compiling…" : "Compile"}
        </button>
        <button className="mini" onClick={() => void runReview()} disabled={reviewing}>
          {reviewing ? "Running review… (up to 2 min)" : "Run receipt review"}
        </button>
        {(compiling || reviewing) && <span className="spinner" />}
        {err && <span className="badge red" title={err}>{err.slice(0, 80)}</span>}
      </div>

      <div className="review-baseline">
        <b>Shipped-atlas baseline:</b> wpc {SHIPPED.wpc} · height {SHIPPED.height} · density{" "}
        {SHIPPED.density} · {SHIPPED.note}
      </div>

      {compileRes && (
        <section className="review-block">
          <h3>Compile · {coverage} glyphs</h3>
          <img className="sheet" src={compileRes.sheet} alt="compiled sheet" />
          <pre className="log">{compileRes.log}</pre>
        </section>
      )}

      {reviewRes && (
        <section className="review-block">
          <h3>Receipt review</h3>
          {s && (
            <div className="ratios">
              <Ratio label="wpc" v={s.wpc_ratio_median} />
              <Ratio label="height" v={s.height_ratio_median} />
              <Ratio label="density" v={s.density_ratio_median} />
              <div className="sev">
                {Object.entries(s.severity_counts || {}).map(([k, v]) => (
                  <span key={k} className={`badge sev-${k}`}>
                    {k}: {v}
                  </span>
                ))}
              </div>
            </div>
          )}
          <img className="sheet" src={reviewRes.png} alt="review render" />
          <pre className="log">{reviewRes.log}</pre>
        </section>
      )}
    </div>
  );
}

function Ratio({ label, v }: { label: string; v: number }) {
  return (
    <div className={`ratio ${ratioClass(v)}`}>
      <div className="ratio-label">{label}</div>
      <div className="ratio-val">{v?.toFixed(3)}</div>
    </div>
  );
}
