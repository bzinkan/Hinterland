/**
 * Persisted Sanctuary diorama preferences: the renderer crash latch and the
 * kid-facing "Simple view" toggle. Stored as a tiny JSON file in the app
 * document directory -- local-only, no PII, no analytics.
 *
 * The store hydrates asynchronously on first import; until then the defaults
 * (no crashes, diorama allowed) apply, which is safe because a crash during
 * that window still gets recorded and write-through persisted.
 */

import { File, Paths } from "expo-file-system";
import { create } from "zustand";

type PersistedPrefs = {
  crashCount: number;
  simpleView: boolean;
};

const DEFAULTS: PersistedPrefs = { crashCount: 0, simpleView: false };

const PREFS_FILE_NAME = "sanctuary-diorama-prefs.json";

function prefsFile(): File {
  return new File(Paths.document, PREFS_FILE_NAME);
}

async function loadPersisted(): Promise<PersistedPrefs> {
  try {
    const file = prefsFile();
    if (!file.exists) return DEFAULTS;
    const parsed = JSON.parse(await file.text()) as Partial<PersistedPrefs>;
    return {
      crashCount:
        typeof parsed.crashCount === "number" && parsed.crashCount >= 0
          ? parsed.crashCount
          : 0,
      simpleView: parsed.simpleView === true,
    };
  } catch {
    return DEFAULTS;
  }
}

function persist(prefs: PersistedPrefs): void {
  try {
    const file = prefsFile();
    file.write(JSON.stringify(prefs));
  } catch {
    // Persistence is best-effort; the in-memory latch still protects the
    // current session.
  }
}

type PrefsState = PersistedPrefs & {
  hydrated: boolean;
  recordRenderCrash: () => void;
  resetRenderCrashes: () => void;
  setSimpleView: (value: boolean) => void;
};

export const useSanctuaryDioramaPrefs = create<PrefsState>((set, get) => ({
  ...DEFAULTS,
  hydrated: false,
  recordRenderCrash: () => {
    const next = { crashCount: get().crashCount + 1, simpleView: get().simpleView };
    set(next);
    persist(next);
  },
  resetRenderCrashes: () => {
    const next = { crashCount: 0, simpleView: get().simpleView };
    set(next);
    persist(next);
  },
  setSimpleView: (value: boolean) => {
    const next = { crashCount: get().crashCount, simpleView: value };
    set(next);
    persist(next);
  },
}));

void loadPersisted().then((persisted) => {
  useSanctuaryDioramaPrefs.setState({ ...persisted, hydrated: true });
});
