/**
 * In-memory draft observation state.
 *
 * One draft at a time -- the kid takes a photo, optionally adds metadata,
 * then submits. After successful submit the draft is cleared. After failed
 * submit the draft sticks around so the kid can retry. Slice 3 (upload
 * flow) consumes this; slice 2 (camera capture) writes to it.
 *
 * Not persisted -- a kill of the app discards the draft. Persistent offline
 * queue (per docs/mobile.md, expo-sqlite) is a later milestone.
 */
import { create } from "zustand";

export type DraftPhoto = {
  /** Local file URI on the device (e.g. file:///.../pending/<id>.jpg). */
  localUri: string;
  /** Pixel dimensions after the resize step. */
  width: number;
  height: number;
};

export type Draft = {
  photo: DraftPhoto | null;
};

type DraftActions = {
  setPhoto: (photo: DraftPhoto) => void;
  clear: () => void;
};

export const useDraftStore = create<Draft & DraftActions>((set) => ({
  photo: null,
  setPhoto: (photo) => set({ photo }),
  clear: () => set({ photo: null }),
}));
