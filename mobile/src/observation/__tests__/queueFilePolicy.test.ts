import {
  QUEUE_ORPHAN_MIN_AGE_MS,
  mayDeleteQueueOrphan,
  submissionKeyFromQueueUri,
} from "@/src/observation/queueFilePolicy";

const key = "01J00000000000000000000000";
const uri = `file:///documents/observation-queue/${key}.jpg`;
const now = Date.parse("2026-07-09T12:05:00.000Z");

describe("Observation queue file ownership policy", () => {
  it("recognizes only canonical queue JPEG names", () => {
    expect(submissionKeyFromQueueUri(uri)).toBe(key);
    expect(submissionKeyFromQueueUri("file:///documents/random.jpg")).toBeNull();
  });

  it("preserves database-owned, active, young, and unknown-age photos", () => {
    expect(
      mayDeleteQueueOrphan(
        { uri, modificationTime: now - QUEUE_ORPHAN_MIN_AGE_MS * 2 },
        new Set([uri]),
        new Set(),
        now,
      ),
    ).toBe(false);
    expect(
      mayDeleteQueueOrphan(
        { uri, modificationTime: now - QUEUE_ORPHAN_MIN_AGE_MS * 2 },
        new Set(),
        new Set([key]),
        now,
      ),
    ).toBe(false);
    expect(
      mayDeleteQueueOrphan(
        { uri, modificationTime: now - 1_000 },
        new Set(),
        new Set(),
        now,
      ),
    ).toBe(false);
    expect(
      mayDeleteQueueOrphan(
        { uri, modificationTime: null },
        new Set(),
        new Set(),
        now,
      ),
    ).toBe(false);
  });

  it("deletes only an old unreferenced canonical child photo", () => {
    expect(
      mayDeleteQueueOrphan(
        { uri, modificationTime: now - QUEUE_ORPHAN_MIN_AGE_MS },
        new Set(),
        new Set(),
        now,
      ),
    ).toBe(true);
  });
});
