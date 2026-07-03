import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useStore } from "../store";
import { api, fetchSampleMeta, type SampleMeta } from "../api";
import type { FontSource, GlyphSource, Node, Stroke } from "../types";
import {
  segmentControls,
  splitCubic,
  nearestTOnSegment,
  strokePathD,
  strokesBounds,
} from "../geometry/bezier";

const PX = 1000 / 60; // 1 px at refCap 60 = 16.667 cap units

interface ViewBox {
  x: number;
  y: number;
  w: number;
  h: number;
}
const BASE_VB: ViewBox = { x: -150, y: -1200, w: 1400, h: 1700 };

type NodeId = string; // `${si}:${ni}`
const nid = (si: number, ni: number): NodeId => `${si}:${ni}`;
const parseNid = (id: NodeId): [number, number] => {
  const [a, b] = id.split(":");
  return [Number(a), Number(b)];
};

interface Layers {
  onion: boolean;
  onionOp: number;
  overlay: boolean;
  overlayOp: number;
  ink: boolean;
  guides: boolean;
}

// snap a cap-unit value to a pixel center (multiples of PX offset by PX/2)
function pixelSnap(v: number): number {
  return Math.round((v - PX / 2) / PX) * PX + PX / 2;
}

// Measure the horizontal center of a sample PNG's ink, returned in cap units
// (relative to the image's own left edge). Returns null if the sample is blank.
async function measureInkCenterCap(url: string, refCap: number): Promise<number | null> {
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    const cv = document.createElement("canvas");
    cv.width = img.naturalWidth;
    cv.height = img.naturalHeight;
    const ctx = cv.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(img, 0, 0);
    const { data } = ctx.getImageData(0, 0, cv.width, cv.height);
    let minC = Infinity;
    let maxC = -1;
    for (let x = 0; x < cv.width; x++) {
      for (let y = 0; y < cv.height; y++) {
        if (data[(y * cv.width + x) * 4 + 3] > 24) {
          if (x < minC) minC = x;
          if (x > maxC) maxC = x;
          break;
        }
      }
    }
    if (maxC < 0) return null;
    const centerPx = (minC + maxC) / 2;
    return (centerPx / refCap) * 1000;
  } catch {
    return null;
  }
}

export function GlyphEditor() {
  const store = useStore();
  const { state } = store;
  const cp = state.currentCp;
  const glyph = state.glyphs[cp];
  const font = state.font!;
  const dot = font.params.dot.size;
  const weight = font.params.weight;

  const svgRef = useRef<SVGSVGElement>(null);
  const [vb, setVb] = useState<ViewBox>(BASE_VB);
  const [layers, setLayers] = useState<Layers>({
    onion: true,
    onionOp: 0.4,
    overlay: true,
    overlayOp: 0.55,
    ink: true,
    guides: true,
  });
  const [pixelGrid, setPixelGrid] = useState(false);
  const [snap, setSnap] = useState(false);
  const [sel, setSel] = useState<Set<NodeId>>(new Set());
  const [marquee, setMarquee] = useState<null | { x0: number; y0: number; x1: number; y1: number }>(null);
  const [xNudge, setXNudge] = useState(0); // manual offset on top of auto-center
  const [sampleMode, setSampleMode] = useState<"median" | "index">("median");
  const [sampleI, setSampleI] = useState(0);
  const [sample, setSample] = useState<SampleMeta | null>(null);
  // ink horizontal center of the current sample, in cap units within the image
  const [sampleInkCenter, setSampleInkCenter] = useState<number | null>(null);
  const [raster, setRaster] = useState<{ url: string; w: number; h: number; off: number } | null>(null);
  const [penMode, setPenMode] = useState(false);
  const penRef = useRef<{ si: number } | null>(null);
  const [cursor, setCursor] = useState<{ x: number; y: number }>({ x: 0, y: 0 });

  const spaceDown = useRef(false);
  const drag = useRef<null | {
    kind: "anchor" | "handle" | "pan" | "marquee";
    startCap: { x: number; y: number };
    startVb?: ViewBox;
    handleOf?: { si: number; ni: number; which: "hIn" | "hOut" };
    origNodes?: Record<NodeId, Node>;
    origHandle?: { x: number; y: number };
    origAnchor?: { x: number; y: number };
    origOpp?: { x: number; y: number };
    breakMirror?: boolean;
    pushed?: boolean;
  }>(null);

  // ---- coordinate helpers ----
  const toCap = useCallback((clientX: number, clientY: number) => {
    const svg = svgRef.current!;
    const pt = new DOMPoint(clientX, clientY).matrixTransform(
      svg.getScreenCTM()!.inverse(),
    );
    return { x: pt.x, y: -pt.y }; // flip: cap-space is y-up
  }, []);

  // ---- onion sample fetch + ink-center measurement ----
  useEffect(() => {
    let live = true;
    setSampleInkCenter(null);
    fetchSampleMeta(cp, sampleMode, sampleI)
      .then(async (m) => {
        if (!live) return;
        setSample(m);
        const center = await measureInkCenterCap(m.url, m.refCap);
        if (live) setSampleInkCenter(center);
      })
      .catch(() => live && setSample(null));
    return () => {
      live = false;
    };
  }, [cp, sampleMode, sampleI]);

  // ---- debounced raster overlay ----
  const glyphKey = glyph ? JSON.stringify(glyph.strokes) + glyph.width : "";
  const paramKey = JSON.stringify(font.params);
  useEffect(() => {
    if (!glyph) {
      setRaster(null);
      return;
    }
    const t = setTimeout(async () => {
      try {
        const r = await api.raster(glyph, font.params, 60);
        const url = `data:image/png;base64,${r.png_b64}`;
        setRaster({ url, w: r.w, h: r.h, off: r.off });
      } catch {
        /* ignore transient raster errors */
      }
    }, 150);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [glyphKey, paramKey, cp]);

  // ---- glyph mutation helpers ----
  const mutate = useCallback(
    (fn: (g: GlyphSource) => GlyphSource, pushUndo = true) => {
      if (pushUndo) store.pushUndo(cp);
      store.setGlyph(cp, fn);
    },
    [store, cp],
  );

  const moveSelected = useCallback(
    (dx: number, dy: number, push = true) => {
      if (sel.size === 0) return;
      mutate((g) => {
        const strokes = g.strokes.map((s, si) => ({
          ...s,
          nodes: s.nodes.map((n, ni) => {
            if (!sel.has(nid(si, ni))) return n;
            const nx = n.x + dx;
            const ny = n.y + dy;
            const shift = { x: nx - n.x, y: ny - n.y };
            return {
              ...n,
              x: nx,
              y: ny,
              hIn: n.hIn ? { x: n.hIn.x + shift.x, y: n.hIn.y + shift.y } : n.hIn,
              hOut: n.hOut ? { x: n.hOut.x + shift.x, y: n.hOut.y + shift.y } : n.hOut,
            };
          }),
        }));
        return { ...g, strokes };
      }, push);
    },
    [sel, mutate],
  );

  // ---- pointer handlers ----
  const onAnchorDown = (e: React.PointerEvent, si: number, ni: number) => {
    if (penMode) return;
    e.stopPropagation();
    (e.target as Element).setPointerCapture?.(e.pointerId);
    const id = nid(si, ni);
    let nextSel = sel;
    if (e.shiftKey) {
      nextSel = new Set(sel);
      nextSel.has(id) ? nextSel.delete(id) : nextSel.add(id);
      setSel(nextSel);
    } else if (!sel.has(id)) {
      nextSel = new Set([id]);
      setSel(nextSel);
    }
    const startCap = toCap(e.clientX, e.clientY);
    const orig: Record<NodeId, Node> = {};
    for (const sid of nextSel) {
      const [s, n] = parseNid(sid);
      orig[sid] = glyph!.strokes[s].nodes[n];
    }
    drag.current = { kind: "anchor", startCap, origNodes: orig, pushed: false };
  };

  const onHandleDown = (
    e: React.PointerEvent,
    si: number,
    ni: number,
    which: "hIn" | "hOut",
  ) => {
    e.stopPropagation();
    (e.target as Element).setPointerCapture?.(e.pointerId);
    const node = glyph!.strokes[si].nodes[ni];
    const startCap = toCap(e.clientX, e.clientY);
    drag.current = {
      kind: "handle",
      startCap,
      handleOf: { si, ni, which },
      origHandle: which === "hIn" ? node.hIn : node.hOut,
      origAnchor: { x: node.x, y: node.y },
      origOpp: which === "hIn" ? node.hOut : node.hIn,
      breakMirror: e.altKey,
      pushed: false,
    };
  };

  const onBgDown = (e: React.PointerEvent) => {
    const capPt = toCap(e.clientX, e.clientY);
    if (penMode) {
      handlePenClick(capPt, e.shiftKey);
      return;
    }
    if (spaceDown.current) {
      drag.current = { kind: "pan", startCap: capPt, startVb: { ...vb } };
      return;
    }
    if (!e.shiftKey) setSel(new Set());
    setMarquee({ x0: capPt.x, y0: capPt.y, x1: capPt.x, y1: capPt.y });
    drag.current = { kind: "marquee", startCap: capPt };
  };

  const onMove = (e: React.PointerEvent) => {
    const capPt = toCap(e.clientX, e.clientY);
    setCursor(capPt);
    const d = drag.current;
    if (!d) return;
    if (d.kind === "pan" && d.startVb) {
      // pan uses svg-space delta; cap y is negated
      const dx = capPt.x - d.startCap.x;
      const dy = capPt.y - d.startCap.y;
      setVb({ ...d.startVb, x: d.startVb.x - dx, y: d.startVb.y + dy });
      return;
    }
    if (d.kind === "marquee") {
      setMarquee((m) => (m ? { ...m, x1: capPt.x, y1: capPt.y } : m));
      return;
    }
    let dx = capPt.x - d.startCap.x;
    let dy = capPt.y - d.startCap.y;
    if (!d.pushed) {
      store.pushUndo(cp);
      d.pushed = true;
    }
    if (d.kind === "anchor" && d.origNodes) {
      store.setGlyph(cp, (g) => {
        const strokes = g.strokes.map((s, si) => ({
          ...s,
          nodes: s.nodes.map((n, ni) => {
            const id = nid(si, ni);
            const o = d.origNodes![id];
            if (!o) return n;
            let nx = o.x + dx;
            let ny = o.y + dy;
            if (snap) {
              nx = pixelSnap(nx);
              ny = pixelSnap(ny);
            }
            const sx = nx - o.x;
            const sy = ny - o.y;
            return {
              ...n,
              x: nx,
              y: ny,
              hIn: o.hIn ? { x: o.hIn.x + sx, y: o.hIn.y + sy } : n.hIn,
              hOut: o.hOut ? { x: o.hOut.x + sx, y: o.hOut.y + sy } : n.hOut,
            };
          }),
        }));
        return { ...g, strokes };
      });
    } else if (d.kind === "handle" && d.handleOf && d.origHandle && d.origAnchor) {
      const { si, ni, which } = d.handleOf;
      let hx = d.origHandle.x + dx;
      let hy = d.origHandle.y + dy;
      if (snap) {
        hx = pixelSnap(hx);
        hy = pixelSnap(hy);
      }
      store.setGlyph(cp, (g) => {
        const strokes = g.strokes.map((s, sidx) => {
          if (sidx !== si) return s;
          return {
            ...s,
            nodes: s.nodes.map((n, nidx) => {
              if (nidx !== ni) return n;
              const patch: Node = { ...n, [which]: { x: hx, y: hy } };
              const smooth = n.type === "smooth" && !d.breakMirror;
              if (smooth && d.origOpp) {
                // mirror opposite: same angle opposite dir, keep its own length
                const ax = d.origAnchor!.x;
                const ay = d.origAnchor!.y;
                const vx = hx - ax;
                const vy = hy - ay;
                const vlen = Math.hypot(vx, vy) || 1;
                const opp = which === "hIn" ? "hOut" : "hIn";
                const oppLen = Math.hypot(d.origOpp.x - ax, d.origOpp.y - ay);
                patch[opp] = {
                  x: ax - (vx / vlen) * oppLen,
                  y: ay - (vy / vlen) * oppLen,
                };
              }
              return patch;
            }),
          };
        });
        return { ...g, strokes };
      });
    }
  };

  const onUp = (e: React.PointerEvent) => {
    const d = drag.current;
    if (d?.kind === "marquee" && marquee) {
      const minX = Math.min(marquee.x0, marquee.x1);
      const maxX = Math.max(marquee.x0, marquee.x1);
      const minY = Math.min(marquee.y0, marquee.y1);
      const maxY = Math.max(marquee.y0, marquee.y1);
      const picked = new Set<NodeId>(e.shiftKey ? sel : []);
      glyph?.strokes.forEach((s, si) =>
        s.nodes.forEach((n, ni) => {
          if (n.x >= minX && n.x <= maxX && n.y >= minY && n.y <= maxY)
            picked.add(nid(si, ni));
        }),
      );
      setSel(picked);
    }
    setMarquee(null);
    drag.current = null;
  };

  // ---- double click a segment → insert node ----
  const onSvgDoubleClick = (e: React.MouseEvent) => {
    if (!glyph || penMode) return;
    const capPt = toCap(e.clientX, e.clientY);
    let best: { si: number; ni: number; t: number; dist: number } | null = null;
    glyph.strokes.forEach((s, si) => {
      const segCount = s.closed ? s.nodes.length : s.nodes.length - 1;
      for (let i = 0; i < segCount; i++) {
        const a = s.nodes[i];
        const b = s.nodes[(i + 1) % s.nodes.length];
        const t = nearestTOnSegment(a, b, capPt);
        const { p0, c1, c2, p3 } = segmentControls(a, b);
        const u = 1 - t;
        const px = u * u * u * p0.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * p3.x;
        const py = u * u * u * p0.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * p3.y;
        const dist = Math.hypot(px - capPt.x, py - capPt.y);
        if (!best || dist < best.dist) best = { si, ni: i, t, dist };
      }
    });
    // cast: closure assignments are invisible to CFA, which narrows `best` to null
    const hit = best as { si: number; ni: number; t: number; dist: number } | null;
    if (!hit || hit.dist > 80) return;
    insertNode(hit.si, hit.ni, hit.t);
  };

  const insertNode = (si: number, ni: number, t: number) => {
    mutate((g) => {
      const s = g.strokes[si];
      const a = s.nodes[ni];
      const b = s.nodes[(ni + 1) % s.nodes.length];
      const { p0, c1, c2, p3, isLine } = segmentControls(a, b);
      const strokes = g.strokes.map((str, i) => {
        if (i !== si) return str;
        const nodes = [...str.nodes];
        if (isLine) {
          const nx = p0.x + (p3.x - p0.x) * t;
          const ny = p0.y + (p3.y - p0.y) * t;
          nodes.splice(ni + 1, 0, { x: nx, y: ny, type: "corner" });
        } else {
          const { left, right, point } = splitCubic(p0, c1, c2, p3, t);
          nodes[ni] = { ...a, hOut: { x: left.c1.x, y: left.c1.y } };
          const bIdx = (ni + 1) % str.nodes.length;
          nodes[bIdx] = { ...b, hIn: { x: right.c2.x, y: right.c2.y } };
          nodes.splice(ni + 1, 0, {
            x: point.x,
            y: point.y,
            type: "smooth",
            hIn: { x: left.c2.x, y: left.c2.y },
            hOut: { x: right.c1.x, y: right.c1.y },
          });
        }
        return { ...str, nodes };
      });
      return { ...g, strokes };
    });
  };

  // ---- pen (add stroke) ----
  const startPen = () => {
    setPenMode(true);
    setSel(new Set());
    penRef.current = null;
  };
  const handlePenClick = (pt: { x: number; y: number }, _shift: boolean) => {
    mutate((g) => {
      const strokes = g.strokes.map((s) => ({ ...s, nodes: [...s.nodes] }));
      if (penRef.current == null) {
        strokes.push({ closed: false, nodes: [{ x: pt.x, y: pt.y, type: "corner" }] });
        penRef.current = { si: strokes.length - 1 };
      } else {
        const s = strokes[penRef.current.si];
        // close if near first node
        const first = s.nodes[0];
        if (s.nodes.length >= 2 && Math.hypot(first.x - pt.x, first.y - pt.y) < 40) {
          s.closed = true;
        } else {
          s.nodes.push({ x: pt.x, y: pt.y, type: "corner" });
        }
      }
      return { ...g, strokes };
    });
  };
  const endPen = useCallback(() => {
    setPenMode(false);
    penRef.current = null;
  }, []);

  // ---- keyboard ----
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (document.activeElement?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      const meta = e.metaKey || e.ctrlKey;
      if (e.code === "Space") {
        spaceDown.current = true;
        return;
      }
      if (penMode && (e.key === "Enter" || e.key === "Escape")) {
        endPen();
        e.preventDefault();
        return;
      }
      if (meta && e.key.toLowerCase() === "z") {
        e.preventDefault();
        e.shiftKey ? store.redo(cp) : store.undo(cp);
        return;
      }
      if (meta && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void store.saveGlyph(cp);
        return;
      }
      if (e.key === "Escape") {
        setSel(new Set());
        return;
      }
      if (e.key === "Delete" || e.key === "Backspace") {
        if (sel.size) {
          e.preventDefault();
          deleteSelected();
        }
        return;
      }
      if (e.key === "[") {
        setSampleMode("index");
        setSampleI((i) => Math.max(0, i - 1));
        return;
      }
      if (e.key === "]") {
        setSampleMode("index");
        setSampleI((i) => i + 1);
        return;
      }
      if (e.key.toLowerCase() === "p") {
        setPixelGrid((v) => !v);
        setSnap((v) => !v);
        return;
      }
      if (e.key.toLowerCase() === "l") {
        convertSelectedSegments("line");
        return;
      }
      if (e.key.toLowerCase() === "c" && !meta) {
        convertSelectedSegments("cubic");
        return;
      }
      const step = e.shiftKey ? PX : 1;
      if (e.key === "ArrowLeft") {
        if (sel.size === 0) {
          store.setCurrentCp(prevCp(cp, state.glyphs));
        } else {
          e.preventDefault();
          moveSelected(-step, 0);
        }
        return;
      }
      if (e.key === "ArrowRight") {
        if (sel.size === 0) {
          store.setCurrentCp(nextCp(cp, state.glyphs));
        } else {
          e.preventDefault();
          moveSelected(step, 0);
        }
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        moveSelected(0, step);
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        moveSelected(0, -step);
        return;
      }
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.code === "Space") spaceDown.current = false;
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keyup", onKeyUp);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cp, sel, penMode, state.glyphs, moveSelected]);

  const deleteSelected = () => {
    mutate((g) => {
      const strokes: Stroke[] = [];
      g.strokes.forEach((s, si) => {
        const keep = s.nodes.filter((_, ni) => !sel.has(nid(si, ni)));
        if (keep.length >= 2) strokes.push({ ...s, nodes: keep });
        else if (keep.length === s.nodes.length) strokes.push(s);
        // fewer than 2 remaining → drop the stroke
      });
      return { ...g, strokes };
    });
    setSel(new Set());
  };

  const convertSelectedSegments = (to: "line" | "cubic") => {
    if (sel.size < 2) return;
    mutate((g) => {
      const strokes = g.strokes.map((s, si) => {
        const nodes = s.nodes.map((n) => ({ ...n }));
        const segCount = s.closed ? nodes.length : nodes.length - 1;
        for (let i = 0; i < segCount; i++) {
          const ai = i;
          const bi = (i + 1) % nodes.length;
          if (!sel.has(nid(si, ai)) || !sel.has(nid(si, bi))) continue;
          if (to === "line") {
            delete nodes[ai].hOut;
            delete nodes[bi].hIn;
          } else {
            const a = nodes[ai];
            const b = nodes[bi];
            nodes[ai].hOut = { x: a.x + (b.x - a.x) / 3, y: a.y + (b.y - a.y) / 3 };
            nodes[bi].hIn = { x: b.x - (b.x - a.x) / 3, y: b.y - (b.y - a.y) / 3 };
          }
        }
        return { ...s, nodes };
      });
      return { ...g, strokes };
    });
  };

  // ---- wheel zoom ----
  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const capPt = toCap(e.clientX, e.clientY);
    const factor = e.deltaY > 0 ? 1.1 : 1 / 1.1;
    const svgFocusX = capPt.x;
    const svgFocusY = -capPt.y;
    setVb((v) => {
      const nw = Math.min(6000, Math.max(300, v.w * factor));
      const nh = nw * (v.h / v.w);
      const nx = svgFocusX - ((svgFocusX - v.x) * nw) / v.w;
      const ny = svgFocusY - ((svgFocusY - v.y) * nh) / v.h;
      return { x: nx, y: ny, w: nw, h: nh };
    });
  };

  // ---- geometry derived ----
  const inkLeft = useMemo(() => {
    if (!glyph) return 0;
    const b = strokesBounds(glyph.strokes);
    return b ? b.minX - dot / 2 : 0;
  }, [glyph, dot]);

  // stroke-geometry horizontal center of the current glyph (for onion auto-center)
  const glyphCenter = useMemo(() => {
    if (!glyph) return 0;
    const b = strokesBounds(glyph.strokes);
    return b ? (b.minX + b.maxX) / 2 : glyph.width / 2;
  }, [glyph]);

  const mk = vb.w / 90; // marker size in cap units (~constant screen px)

  if (!glyph) {
    return (
      <div className="editor missing-glyph">
        <div className="pad">
          <p>
            No source for <b>{String.fromCodePoint(cp)}</b> (U+
            {cp.toString(16).padStart(4, "0")}).
          </p>
          {state.pendingTrace.includes(cp) ? (
            <button onClick={() => void store.adoptTrace(cp)}>Adopt pending trace</button>
          ) : (
            <button onClick={() => void api.trace(String.fromCodePoint(cp)).then(() => store.reload())}>
              Trace this glyph
            </button>
          )}
          <EditorNav cp={cp} store={store} />
        </div>
      </div>
    );
  }

  // onion image placement (svg space, non-flipped)
  const onionEl =
    sample && layers.onion ? (() => {
      const rc = sample.refCap;
      const imgH = (120 / rc) * 1000;
      const imgW = (80 / rc) * 1000;
      const topCap = (sample.baselineRow / rc) * 1000; // cap-units above baseline
      // auto-center: align the sample's ink center to the glyph's stroke center;
      // fall back to the image geometric center if the sample is blank.
      const inkCenter = sampleInkCenter ?? imgW / 2;
      const autoX = glyphCenter - inkCenter;
      return (
        <image
          href={sample.url}
          x={autoX + xNudge}
          y={-topCap}
          width={imgW}
          height={imgH}
          opacity={layers.onionOp}
          style={{ imageRendering: "pixelated" }}
          preserveAspectRatio="none"
        />
      );
    })() : null;

  // raster overlay placement (svg space)
  const overlayEl =
    raster && layers.overlay ? (() => {
      const bottomCap = -raster.off * PX;
      const topCap = bottomCap + raster.h * PX;
      return (
        <image
          href={raster.url}
          x={inkLeft}
          y={-topCap}
          width={raster.w * PX}
          height={raster.h * PX}
          opacity={layers.overlayOp}
          style={{ imageRendering: "pixelated", filter: "hue-rotate(180deg) saturate(3)" }}
          preserveAspectRatio="none"
        />
      );
    })() : null;

  return (
    <div className="editor">
      <div className="canvas-wrap">
        <svg
          ref={svgRef}
          className="canvas"
          viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`}
          onPointerDown={onBgDown}
          onPointerMove={onMove}
          onPointerUp={onUp}
          onDoubleClick={onSvgDoubleClick}
          onWheel={onWheel}
          style={{ cursor: penMode ? "crosshair" : spaceDown.current ? "grab" : "default" }}
        >
          {/* background catch rect */}
          <rect x={vb.x} y={vb.y} width={vb.w} height={vb.h} fill="#141414" />
          {onionEl}
          {overlayEl}
          {layers.guides && <Guides vb={vb} font={font} dot={dot} width={glyph.width} pixelGrid={pixelGrid} />}
          <g transform="scale(1,-1)">
            {/* ink preview */}
            {layers.ink &&
              glyph.strokes.map((s, i) => (
                <path
                  key={`ink${i}`}
                  d={strokePathD(s)}
                  fill="none"
                  stroke="#1E88E5"
                  strokeOpacity={0.4}
                  strokeWidth={dot * weight}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              ))}
            {/* hairline centerlines */}
            {glyph.strokes.map((s, i) => (
              <path
                key={`hair${i}`}
                d={strokePathD(s)}
                fill="none"
                stroke="#7ec8ff"
                strokeWidth={mk * 0.12}
              />
            ))}
            {/* nodes + handles */}
            {glyph.strokes.map((s, si) =>
              s.nodes.map((n, ni) => {
                const id = nid(si, ni);
                const selected = sel.has(id);
                return (
                  <g key={id}>
                    {n.hIn && (
                      <>
                        <line x1={n.x} y1={n.y} x2={n.hIn.x} y2={n.hIn.y} stroke="#888" strokeWidth={mk * 0.1} />
                        <circle
                          cx={n.hIn.x}
                          cy={n.hIn.y}
                          r={mk * 0.5}
                          fill="#ffb300"
                          onPointerDown={(e) => onHandleDown(e, si, ni, "hIn")}
                          style={{ cursor: "move" }}
                        />
                      </>
                    )}
                    {n.hOut && (
                      <>
                        <line x1={n.x} y1={n.y} x2={n.hOut.x} y2={n.hOut.y} stroke="#888" strokeWidth={mk * 0.1} />
                        <circle
                          cx={n.hOut.x}
                          cy={n.hOut.y}
                          r={mk * 0.5}
                          fill="#ffb300"
                          onPointerDown={(e) => onHandleDown(e, si, ni, "hOut")}
                          style={{ cursor: "move" }}
                        />
                      </>
                    )}
                    {n.type === "smooth" ? (
                      <circle
                        cx={n.x}
                        cy={n.y}
                        r={mk * 0.6}
                        fill={selected ? "#1E88E5" : "#eee"}
                        stroke="#111"
                        strokeWidth={mk * 0.08}
                        onPointerDown={(e) => onAnchorDown(e, si, ni)}
                        style={{ cursor: "move" }}
                      />
                    ) : (
                      <rect
                        x={n.x - mk * 0.55}
                        y={n.y - mk * 0.55}
                        width={mk * 1.1}
                        height={mk * 1.1}
                        fill={selected ? "#1E88E5" : "#eee"}
                        stroke="#111"
                        strokeWidth={mk * 0.08}
                        onPointerDown={(e) => onAnchorDown(e, si, ni)}
                        style={{ cursor: "move" }}
                      />
                    )}
                  </g>
                );
              }),
            )}
            {/* marquee */}
            {marquee && (
              <rect
                x={Math.min(marquee.x0, marquee.x1)}
                y={Math.min(marquee.y0, marquee.y1)}
                width={Math.abs(marquee.x1 - marquee.x0)}
                height={Math.abs(marquee.y1 - marquee.y0)}
                fill="#1E88E5"
                fillOpacity={0.12}
                stroke="#1E88E5"
                strokeWidth={mk * 0.15}
              />
            )}
          </g>
        </svg>
        <StatusBar
          cursor={cursor}
          sel={sel}
          glyph={glyph}
          store={store}
          cp={cp}
          dirty={state.dirty.has(cp)}
          sampleMode={sampleMode}
          sampleI={sampleI}
          sampleN={sample?.n ?? 0}
        />
      </div>

      <aside className="rpanel">
        <RightPanel
          store={store}
          cp={cp}
          glyph={glyph}
          layers={layers}
          setLayers={setLayers}
          xNudge={xNudge}
          setXNudge={setXNudge}
          pixelGrid={pixelGrid}
          setPixelGrid={setPixelGrid}
          snap={snap}
          setSnap={setSnap}
          sel={sel}
          setSel={setSel}
          penMode={penMode}
          startPen={startPen}
          endPen={endPen}
        />
      </aside>
    </div>
  );
}

// prev/next codepoint that has a glyph (fallback to any 33..126)
function prevCp(cp: number, glyphs: Record<number, GlyphSource>): number {
  for (let c = cp - 1; c >= 33; c--) if (glyphs[c]) return c;
  for (let c = 126; c > cp; c--) if (glyphs[c]) return c;
  return cp;
}
function nextCp(cp: number, glyphs: Record<number, GlyphSource>): number {
  for (let c = cp + 1; c <= 126; c++) if (glyphs[c]) return c;
  for (let c = 33; c < cp; c++) if (glyphs[c]) return c;
  return cp;
}

function Guides({
  vb,
  font,
  dot,
  width,
  pixelGrid,
}: {
  vb: ViewBox;
  font: FontSource;
  dot: number;
  width: number;
  pixelGrid: boolean;
}) {
  const lines: { y: number; c: string; label: string }[] = [
    { y: 0, c: "#4a4a4a", label: "base" },
    { y: font.metrics.xHeight, c: "#333", label: "x" },
    { y: font.metrics.capHeight, c: "#3a3a3a", label: "cap" },
    { y: font.metrics.ascender, c: "#2a2a2a", label: "asc" },
    { y: font.metrics.descender, c: "#2a2a2a", label: "desc" },
  ];
  const x0 = vb.x;
  const x1 = vb.x + vb.w;
  const pixLines = [];
  if (pixelGrid) {
    for (let x = Math.floor(vb.x / PX) * PX; x < vb.x + vb.w; x += PX)
      pixLines.push(x);
  }
  return (
    <g transform="scale(1,-1)">
      {pixelGrid &&
        pixLines.map((x, i) => (
          <line key={`px${i}`} x1={x} y1={-vb.y - vb.h} x2={x} y2={-vb.y} stroke="#1f1f1f" strokeWidth={2} />
        ))}
      {pixelGrid &&
        (() => {
          const rows = [];
          for (let y = Math.floor(-(vb.y + vb.h) / PX) * PX; y < -vb.y; y += PX)
            rows.push(y);
          return rows.map((y, i) => (
            <line key={`py${i}`} x1={x0} y1={y} x2={x1} y2={y} stroke="#1f1f1f" strokeWidth={2} />
          ));
        })()}
      {lines.map((l) => (
        <g key={l.label}>
          <line x1={x0} y1={l.y} x2={x1} y2={l.y} stroke={l.c} strokeWidth={3} />
          <text x={x0 + 8} y={l.y - 8} fill="#666" fontSize={40} transform={`scale(1,-1) translate(0, ${-2 * l.y})`}>
            {l.label} {l.y}
          </text>
        </g>
      ))}
      {/* inset centerline guides at dot/2 and cap - dot/2 */}
      <line x1={x0} y1={dot / 2} x2={x1} y2={dot / 2} stroke="#3d5a80" strokeWidth={2} strokeDasharray="20 16" />
      <line x1={x0} y1={font.metrics.capHeight - dot / 2} x2={x1} y2={font.metrics.capHeight - dot / 2} stroke="#3d5a80" strokeWidth={2} strokeDasharray="20 16" />
      {/* advance width vertical */}
      <line x1={width} y1={-vb.y - vb.h} x2={width} y2={-vb.y} stroke="#2e5d34" strokeWidth={3} strokeDasharray="24 12" />
      <line x1={0} y1={-vb.y - vb.h} x2={0} y2={-vb.y} stroke="#333" strokeWidth={2} />
    </g>
  );
}

function StatusBar({
  cursor,
  sel,
  glyph,
  store,
  cp,
  dirty,
  sampleMode,
  sampleI,
  sampleN,
}: {
  cursor: { x: number; y: number };
  sel: Set<NodeId>;
  glyph: GlyphSource;
  store: ReturnType<typeof useStore>;
  cp: number;
  dirty: boolean;
  sampleMode: string;
  sampleI: number;
  sampleN: number;
}) {
  const one = sel.size === 1 ? parseNid([...sel][0]) : null;
  const node = one ? glyph.strokes[one[0]]?.nodes[one[1]] : null;
  return (
    <div className="statusbar">
      <span>
        {cursor.x.toFixed(0)}, {cursor.y.toFixed(0)} u ({(cursor.x / PX).toFixed(1)},{" "}
        {(cursor.y / PX).toFixed(1)} px)
      </span>
      <span className="sep">·</span>
      <span>
        {String.fromCodePoint(cp)} U+{cp.toString(16).padStart(4, "0")} · {glyph.provenance}
        {dirty ? " *" : ""}
      </span>
      <span className="sep">·</span>
      <span>{sel.size} sel</span>
      {node && one && (
        <>
          <span className="sep">·</span>
          <label>
            x
            <input
              type="number"
              value={Math.round(node.x)}
              onChange={(e) => {
                const v = Number(e.target.value);
                store.pushUndo(cp);
                store.setGlyph(cp, (g) => patchNode(g, one[0], one[1], { x: v }));
              }}
            />
          </label>
          <label>
            y
            <input
              type="number"
              value={Math.round(node.y)}
              onChange={(e) => {
                const v = Number(e.target.value);
                store.pushUndo(cp);
                store.setGlyph(cp, (g) => patchNode(g, one[0], one[1], { y: v }));
              }}
            />
          </label>
        </>
      )}
      <span className="sep">·</span>
      <span>
        onion {sampleMode}
        {sampleMode === "index" ? ` ${sampleI + 1}/${sampleN}` : ""}
      </span>
    </div>
  );
}

function patchNode(g: GlyphSource, si: number, ni: number, patch: Partial<Node>): GlyphSource {
  const strokes = g.strokes.map((s, i) =>
    i !== si
      ? s
      : { ...s, nodes: s.nodes.map((n, j) => (j !== ni ? n : { ...n, ...patch })) },
  );
  return { ...g, strokes };
}

function EditorNav({ cp, store }: { cp: number; store: ReturnType<typeof useStore> }) {
  return (
    <div className="editnav">
      <button onClick={() => store.setCurrentCp(prevCp(cp, store.state.glyphs))}>← prev</button>
      <button onClick={() => store.setCurrentCp(nextCp(cp, store.state.glyphs))}>next →</button>
    </div>
  );
}

function RightPanel({
  store,
  cp,
  glyph,
  layers,
  setLayers,
  xNudge,
  setXNudge,
  pixelGrid,
  setPixelGrid,
  snap,
  setSnap,
  sel,
  setSel,
  penMode,
  startPen,
  endPen,
}: {
  store: ReturnType<typeof useStore>;
  cp: number;
  glyph: GlyphSource;
  layers: Layers;
  setLayers: (f: (l: Layers) => Layers) => void;
  xNudge: number;
  setXNudge: (v: number) => void;
  pixelGrid: boolean;
  setPixelGrid: (v: boolean) => void;
  snap: boolean;
  setSnap: (v: boolean) => void;
  sel: Set<NodeId>;
  setSel: (s: Set<NodeId>) => void;
  penMode: boolean;
  startPen: () => void;
  endPen: () => void;
}) {
  const font = store.state.font!;
  const [copyFrom, setCopyFrom] = useState("");
  const p = font.params;

  const slider = (
    label: string,
    val: number,
    min: number,
    max: number,
    step: number,
    set: (v: number) => void,
  ) => (
    <label className="ctl">
      <span>
        {label} <b>{val}</b>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={val}
        onChange={(e) => set(Number(e.target.value))}
      />
    </label>
  );

  return (
    <div className="rpanel-inner">
      <section>
        <h3>Layers</h3>
        {(["onion", "overlay", "ink", "guides"] as const).map((k) => (
          <label key={k} className="chk">
            <input
              type="checkbox"
              checked={layers[k] as boolean}
              onChange={() => setLayers((l) => ({ ...l, [k]: !l[k] }))}
            />
            {k}
          </label>
        ))}
        {slider("onion opacity", layers.onionOp, 0, 1, 0.05, (v) => setLayers((l) => ({ ...l, onionOp: v })))}
        {slider("overlay opacity", layers.overlayOp, 0, 1, 0.05, (v) => setLayers((l) => ({ ...l, overlayOp: v })))}
        {slider("onion x-offset (auto-centered)", xNudge, -600, 600, 10, setXNudge)}
        <label className="chk">
          <input type="checkbox" checked={pixelGrid} onChange={() => setPixelGrid(!pixelGrid)} />
          pixel grid (P)
        </label>
        <label className="chk">
          <input type="checkbox" checked={snap} onChange={() => setSnap(!snap)} />
          pixel snap
        </label>
      </section>

      <section>
        <h3>Params (live)</h3>
        {slider("dot size", p.dot.size, 20, 200, 0.1, (v) =>
          store.setFontParams((pp) => ({ ...pp, dot: { ...pp.dot, size: v } })),
        )}
        {slider("dot pitch", p.dot.pitch, 0, 1, 0.01, (v) =>
          store.setFontParams((pp) => ({ ...pp, dot: { ...pp.dot, pitch: v } })),
        )}
        <label className="ctl">
          <span>dot shape</span>
          <select
            value={p.dot.shape}
            onChange={(e) =>
              store.setFontParams((pp) => ({ ...pp, dot: { ...pp.dot, shape: e.target.value as "round" | "square" } }))
            }
          >
            <option value="round">round</option>
            <option value="square">square</option>
          </select>
        </label>
        {slider("weight", p.weight, 0.5, 2, 0.01, (v) => store.setFontParams((pp) => ({ ...pp, weight: v })))}
        <button className="mini" onClick={() => void store.saveFont()} disabled={!store.state.fontDirty}>
          Save font{store.state.fontDirty ? " *" : ""}
        </button>
      </section>

      <section>
        <h3>Glyph</h3>
        <label className="ctl">
          <span>advance width</span>
          <input
            type="number"
            value={Math.round(glyph.width)}
            onChange={(e) => {
              store.pushUndo(cp);
              store.setGlyph(cp, (g) => ({ ...g, width: Number(e.target.value) }));
            }}
          />
        </label>
        <label className="ctl">
          <span>baselineNudgePx</span>
          <input
            type="number"
            step={0.5}
            value={glyph.baselineNudgePx}
            onChange={(e) => {
              store.pushUndo(cp);
              store.setGlyph(cp, (g) => ({ ...g, baselineNudgePx: Number(e.target.value) }));
            }}
          />
        </label>
        <div className="btnrow">
          <button className={penMode ? "mini active" : "mini"} onClick={penMode ? endPen : startPen}>
            {penMode ? "End stroke (Esc)" : "Add stroke"}
          </button>
          <button
            className="mini"
            disabled={sel.size === 0}
            onClick={() => {
              // delete strokes touched by selection
              store.pushUndo(cp);
              const strokeIdx = new Set([...sel].map((id) => parseNid(id)[0]));
              store.setGlyph(cp, (g) => ({
                ...g,
                strokes: g.strokes.filter((_, i) => !strokeIdx.has(i)),
              }));
              setSel(new Set());
            }}
          >
            Delete stroke
          </button>
        </div>
        <div className="btnrow">
          <select value={copyFrom} onChange={(e) => setCopyFrom(e.target.value)}>
            <option value="">Copy strokes from…</option>
            {Object.values(store.state.glyphs)
              .sort((a, b) => a.codepoint - b.codepoint)
              .map((g) => (
                <option key={g.codepoint} value={g.codepoint}>
                  {g.char} (U+{g.codepoint.toString(16).padStart(4, "0")})
                </option>
              ))}
          </select>
          <button
            className="mini"
            disabled={!copyFrom}
            onClick={() => {
              const src = store.state.glyphs[Number(copyFrom)];
              if (!src) return;
              store.pushUndo(cp);
              store.setGlyph(cp, (g) => ({
                ...g,
                strokes: JSON.parse(JSON.stringify(src.strokes)),
              }));
            }}
          >
            Copy
          </button>
        </div>
      </section>

      <section>
        <h3>Save</h3>
        <div className="btnrow">
          <button className="mini" disabled={!store.canUndo(cp)} onClick={() => store.undo(cp)}>
            Undo
          </button>
          <button className="mini" disabled={!store.canRedo(cp)} onClick={() => store.redo(cp)}>
            Redo
          </button>
        </div>
        <button className="save" onClick={() => void store.saveGlyph(cp)} disabled={!store.state.dirty.has(cp)}>
          Save glyph (⌘S){store.state.dirty.has(cp) ? " *" : ""}
        </button>
        <EditorNav cp={cp} store={store} />
      </section>
    </div>
  );
}
