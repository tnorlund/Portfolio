import { useState } from "react";
import { useStore } from "./store";
import { FontGrid } from "./components/FontGrid";
import { GlyphEditor } from "./components/GlyphEditor";
import { SpacingLab } from "./components/SpacingLab";
import { Review } from "./components/Review";

type Tab = "grid" | "glyph" | "spacing" | "review";

const TABS: { id: Tab; label: string; key: string }[] = [
  { id: "grid", label: "Font grid", key: "1" },
  { id: "glyph", label: "Glyph", key: "2" },
  { id: "spacing", label: "Spacing lab", key: "3" },
  { id: "review", label: "Review", key: "4" },
];

export function App() {
  const { state } = useStore();
  const [tab, setTab] = useState<Tab>("glyph");

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          Glyph Studio <span className="muted">· {state.font?.name ?? "…"}</span>
        </div>
        <nav className="tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={tab === t.id ? "tab active" : "tab"}
              onClick={() => setTab(t.id)}
            >
              <span className="tabkey">{t.key}</span>
              {t.label}
            </button>
          ))}
        </nav>
        <div className="status">
          {state.fontDirty && <span className="badge amber">font*</span>}
          {state.dirty.size > 0 && (
            <span className="badge amber">{state.dirty.size} unsaved</span>
          )}
          {state.error && <span className="badge red" title={state.error}>error</span>}
        </div>
      </header>

      <main className="body">
        {!state.loaded && !state.error && <div className="pad">Loading font…</div>}
        {state.error && !state.loaded && (
          <div className="pad error">Failed to load: {state.error}</div>
        )}
        {state.loaded && (
          <>
            {tab === "grid" && <FontGrid onOpen={() => setTab("glyph")} />}
            {tab === "glyph" && <GlyphEditor />}
            {tab === "spacing" && <SpacingLab />}
            {tab === "review" && <Review />}
          </>
        )}
      </main>
    </div>
  );
}
