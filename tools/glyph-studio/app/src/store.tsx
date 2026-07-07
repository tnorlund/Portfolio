import {
  createContext,
  useContext,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api } from "./api";
import type {
  AtlasGlyph,
  FontParams,
  FontSource,
  GlyphSource,
} from "./types";

interface StoreState {
  loaded: boolean;
  error: string | null;
  font: FontSource | null;
  glyphs: Record<number, GlyphSource>;
  pendingTrace: number[]; // codepoints with a _traced/ file waiting to adopt
  dirty: Set<number>;
  fontDirty: boolean;
  currentCp: number;
  atlas: Record<string, AtlasGlyph> | null; // last compile atlas
  saving: Set<number>;
}

const MAX_UNDO = 100;

export interface StoreApi {
  state: StoreState;
  glyph: (cp: number) => GlyphSource | undefined;
  reload: () => Promise<void>;
  setCurrentCp: (cp: number) => void;
  pushUndo: (cp: number) => void;
  setGlyph: (cp: number, updater: (g: GlyphSource) => GlyphSource) => void;
  undo: (cp: number) => void;
  redo: (cp: number) => void;
  canUndo: (cp: number) => boolean;
  canRedo: (cp: number) => boolean;
  saveGlyph: (cp: number) => Promise<void>;
  setFontParams: (updater: (p: FontParams) => FontParams) => void;
  setFontPreview: (updater: (p: FontSource["preview"]) => FontSource["preview"]) => void;
  saveFont: () => Promise<void>;
  adoptTrace: (cp: number) => Promise<void>;
  setAtlas: (a: Record<string, AtlasGlyph>) => void;
}

const Ctx = createContext<StoreApi | null>(null);

export function StoreProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<StoreState>({
    loaded: false,
    error: null,
    font: null,
    glyphs: {},
    pendingTrace: [],
    dirty: new Set(),
    fontDirty: false,
    currentCp: 65, // 'A'
    atlas: null,
    saving: new Set(),
  });

  // Per-glyph undo/redo stacks (JSON snapshots), kept in refs (not render state).
  const undoRef = useRef<Record<number, string[]>>({});
  const redoRef = useRef<Record<number, string[]>>({});

  const reload = useCallback(async () => {
    try {
      const bundle = await api.getFont();
      setState((s) => ({
        ...s,
        loaded: true,
        error: null,
        font: bundle.font,
        glyphs: bundle.glyphs,
        pendingTrace: bundle.traced,
        dirty: new Set(),
        fontDirty: false,
      }));
    } catch (e) {
      setState((s) => ({ ...s, error: String((e as Error).message) }));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const glyph = useCallback(
    (cp: number) => state.glyphs[cp],
    [state.glyphs],
  );

  const setCurrentCp = useCallback((cp: number) => {
    setState((s) => ({ ...s, currentCp: cp }));
  }, []);

  const pushUndo = useCallback((cp: number) => {
    setState((s) => {
      const g = s.glyphs[cp];
      if (!g) return s;
      const stack = undoRef.current[cp] ?? [];
      stack.push(JSON.stringify(g));
      if (stack.length > MAX_UNDO) stack.shift();
      undoRef.current[cp] = stack;
      redoRef.current[cp] = [];
      return s;
    });
  }, []);

  const setGlyph = useCallback(
    (cp: number, updater: (g: GlyphSource) => GlyphSource) => {
      setState((s) => {
        const g = s.glyphs[cp];
        if (!g) return s;
        const next = updater(g);
        const dirty = new Set(s.dirty);
        dirty.add(cp);
        return { ...s, glyphs: { ...s.glyphs, [cp]: next }, dirty };
      });
    },
    [],
  );

  const undo = useCallback((cp: number) => {
    setState((s) => {
      const stack = undoRef.current[cp];
      if (!stack || stack.length === 0) return s;
      const g = s.glyphs[cp];
      const snap = stack.pop()!;
      (redoRef.current[cp] ??= []).push(JSON.stringify(g));
      const dirty = new Set(s.dirty);
      dirty.add(cp);
      return { ...s, glyphs: { ...s.glyphs, [cp]: JSON.parse(snap) }, dirty };
    });
  }, []);

  const redo = useCallback((cp: number) => {
    setState((s) => {
      const stack = redoRef.current[cp];
      if (!stack || stack.length === 0) return s;
      const g = s.glyphs[cp];
      const snap = stack.pop()!;
      (undoRef.current[cp] ??= []).push(JSON.stringify(g));
      const dirty = new Set(s.dirty);
      dirty.add(cp);
      return { ...s, glyphs: { ...s.glyphs, [cp]: JSON.parse(snap) }, dirty };
    });
  }, []);

  const canUndo = useCallback(
    (cp: number) => (undoRef.current[cp]?.length ?? 0) > 0,
    [],
  );
  const canRedo = useCallback(
    (cp: number) => (redoRef.current[cp]?.length ?? 0) > 0,
    [],
  );

  const saveGlyph = useCallback(async (cp: number) => {
    let toSave: GlyphSource | undefined;
    setState((s) => {
      const g = s.glyphs[cp];
      if (!g) return s;
      toSave = { ...g, provenance: "edited" };
      const saving = new Set(s.saving);
      saving.add(cp);
      return { ...s, glyphs: { ...s.glyphs, [cp]: toSave }, saving };
    });
    if (!toSave) return;
    try {
      await api.putGlyph(cp, toSave);
      setState((s) => {
        const dirty = new Set(s.dirty);
        dirty.delete(cp);
        const saving = new Set(s.saving);
        saving.delete(cp);
        return { ...s, dirty, saving };
      });
    } catch (e) {
      setState((s) => {
        const saving = new Set(s.saving);
        saving.delete(cp);
        return { ...s, saving, error: String((e as Error).message) };
      });
    }
  }, []);

  const setFontParams = useCallback((updater: (p: FontParams) => FontParams) => {
    setState((s) => {
      if (!s.font) return s;
      return {
        ...s,
        font: { ...s.font, params: updater(s.font.params) },
        fontDirty: true,
      };
    });
  }, []);

  const setFontPreview = useCallback(
    (updater: (p: FontSource["preview"]) => FontSource["preview"]) => {
      setState((s) => {
        if (!s.font) return s;
        return {
          ...s,
          font: { ...s.font, preview: updater(s.font.preview) },
          fontDirty: true,
        };
      });
    },
    [],
  );

  const saveFont = useCallback(async () => {
    let f: FontSource | null = null;
    setState((s) => {
      f = s.font;
      return s;
    });
    if (!f) return;
    try {
      await api.putFont(f);
      setState((s) => ({ ...s, fontDirty: false }));
    } catch (e) {
      setState((s) => ({ ...s, error: String((e as Error).message) }));
    }
  }, []);

  const adoptTrace = useCallback(
    async (cp: number) => {
      await api.adoptTrace(cp);
      await reload();
    },
    [reload],
  );

  const setAtlas = useCallback((a: Record<string, AtlasGlyph>) => {
    setState((s) => ({ ...s, atlas: a }));
  }, []);

  const value = useMemo<StoreApi>(
    () => ({
      state,
      glyph,
      reload,
      setCurrentCp,
      pushUndo,
      setGlyph,
      undo,
      redo,
      canUndo,
      canRedo,
      saveGlyph,
      setFontParams,
      setFontPreview,
      saveFont,
      adoptTrace,
      setAtlas,
    }),
    [
      state,
      glyph,
      reload,
      setCurrentCp,
      pushUndo,
      setGlyph,
      undo,
      redo,
      canUndo,
      canRedo,
      saveGlyph,
      setFontParams,
      setFontPreview,
      saveFont,
      adoptTrace,
      setAtlas,
    ],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): StoreApi {
  const s = useContext(Ctx);
  if (!s) throw new Error("useStore outside provider");
  return s;
}
