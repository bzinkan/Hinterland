/**
 * In-memory pointer to the durable observation queue.
 *
 * One draft at a time -- the kid takes a photo, optionally adds metadata,
 * then submits. After successful submit the draft is cleared. After failed
 * submit the draft sticks around so the kid can retry. Slice 3 (upload
 * flow) consumes this; slice 2 (camera capture) writes to it.
 *
 * Photo bytes and submission metadata live in SQLite plus document storage;
 * process death only loses this navigation convenience pointer.
 */
import { create } from "zustand";

export type DraftPhoto = {
  /** Local file URI on the device (e.g. file:///.../pending/<id>.jpg). */
  localUri: string;
  /** Pixel dimensions after the resize step. */
  width: number;
  height: number;
  submissionKey: string;
  ownerUserId: string;
  observedAt: string;
  source: "camera" | "library";
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
