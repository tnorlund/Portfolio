import { useMemo, useState } from "react";
import { useStore } from "../store";
import { GlyphThumb } from "./GlyphThumb";

const FILTERS: { id: string; label: string; test: (cp: number) => boolean }[] = [
  { id: "all", label: "All", test: () => true },
  { id: "missing", label: "Missing", test: () => false }, // handled specially
  {
    id: "ucdiag",
    label: "UC diagonals",
    test: (cp) => "HKMNVWXY".includes(String.fromCodePoint(cp)),
  },
  {
    id: "lcdiag",
    label: "lc diagonals",
    test: (cp) => "vwxy".includes(String.fromCodePoint(cp)),
  },
  {
    id: "complex",
    label: "complex",
    test: (cp) => "ghipq".includes(String.fromCodePoint(cp)),
  },
  {
    id: "noisy",
    label: "noisy",
    test: (cp) => '%&@"#$!'.includes(String.fromCodePoint(cp)),
  },
  { id: "digits", label: "digits", test: (cp) => cp >= 48 && cp <= 57 },
  {
    id: "punct",
    label: "punct",
    test: (cp) => {
      const ch = String.fromCodePoint(cp);
      return !/[A-Za-z0-9]/.test(ch);
    },
  },
];

export function FontGrid({ onOpen }: { onOpen: () => void }) {
  const { state, setCurrentCp, adoptTrace } = useStore();
  const [filter, setFilter] = useState("all");
  const dot = state.font?.params.dot.size ?? 98;

  const cps = useMemo(() => {
    const all: number[] = [];
    for (let cp = 33; cp <= 126; cp++) all.push(cp);
    const f = FILTERS.find((x) => x.id === filter)!;
    if (filter === "missing") return all.filter((cp) => !state.glyphs[cp]);
    if (filter === "all") return all;
    return all.filter(f.test);
  }, [filter, state.glyphs]);

  return (
    <div className="grid-tab">
      <div className="chips">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            className={filter === f.id ? "chip active" : "chip"}
            onClick={() => setFilter(f.id)}
          >
            {f.label}
          </button>
        ))}
      </div>
      <div className="glyph-grid">
        {cps.map((cp) => {
          const g = state.glyphs[cp];
          const pending = state.pendingTrace.includes(cp);
          const prov = !g ? "missing" : g.provenance;
          const dirty = state.dirty.has(cp);
          return (
            <div
              key={cp}
              className={`gcell prov-${prov}`}
              onClick={() => {
                setCurrentCp(cp);
                onOpen();
              }}
              title={`${String.fromCodePoint(cp)} · U+${cp
                .toString(16)
                .padStart(4, "0")} · ${prov}`}
            >
              <div className="gcell-char">{String.fromCodePoint(cp)}</div>
              <GlyphThumb glyph={g} dot={dot} size={46} />
              <div className="gcell-foot">
                <span className={`dot prov-${prov}`} />
                {dirty && <span className="dot dirty" title="unsaved" />}
                {pending && (
                  <button
                    className="tracedot"
                    title="pending trace — click to adopt"
                    onClick={(e) => {
                      e.stopPropagation();
                      if (confirm(`Adopt trace for '${String.fromCodePoint(cp)}'?`))
                        void adoptTrace(cp);
                    }}
                  />
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
