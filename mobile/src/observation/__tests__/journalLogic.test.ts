import type { DexListItem } from "@/src/api/dex";
import {
  DEFAULT_JOURNAL_MODE,
  findCountLabel,
  isUrlUsable,
  journalCaption,
  photoDisplayMode,
  speciesDisplayName,
  speciesSubtitle,
} from "@/src/observation/journalLogic";

function dexItem(overrides: Partial<DexListItem> = {}): DexListItem {
  return {
    id: "dex-1",
    taxon_id: 12345,
    species_name: "Cached display",
    common_name: "Yellow Cosmos",
    scientific_name: "Cosmos sulphureus",
    iconic_taxon: "Plantae",
    first_observation_id: "obs-1",
    first_photo_id: "photo-1",
    first_photo_status: "clean",
    first_seen_at: "2026-07-06T12:00:00Z",
    observation_count: 1,
    latest_seen_at: "2026-07-07T12:00:00Z",
    ...overrides,
  };
}

describe("Field Journal display rules", () => {
  test("defaults to photos first", () => {
    expect(DEFAULT_JOURNAL_MODE).toBe("photos");
  });

  test("maps photo statuses to display modes", () => {
    expect(photoDisplayMode("pending")).toBe("image");
    expect(photoDisplayMode("clean")).toBe("image");
    expect(photoDisplayMode("quarantine")).toBe("reviewing");
    expect(photoDisplayMode("deleted")).toBe("removed");
    expect(photoDisplayMode("future-status")).toBe("reviewing");
  });

  test("uses mystery caption for unnamed observations", () => {
    expect(journalCaption(null)).toBe("Mystery find");
    expect(journalCaption("  ")).toBe("Mystery find");
    expect(journalCaption("Yellow Cosmos")).toBe("Yellow Cosmos");
  });

  test("prefers verified species display names in order", () => {
    expect(speciesDisplayName(dexItem())).toBe("Yellow Cosmos");
    expect(speciesDisplayName(dexItem({ common_name: null }))).toBe("Cached display");
    expect(
      speciesDisplayName(
        dexItem({
          common_name: null,
          species_name: null,
        }),
      ),
    ).toBe("Cosmos sulphureus");
    expect(
      speciesDisplayName(
        dexItem({
          common_name: null,
          species_name: null,
          scientific_name: null,
        }),
      ),
    ).toBe("Taxon 12345");
  });

  test("formats species subtitles and counts", () => {
    expect(speciesSubtitle(dexItem())).toBe("Cosmos sulphureus - Plantae");
    expect(
      speciesSubtitle(
        dexItem({
          scientific_name: null,
          iconic_taxon: null,
        }),
      ),
    ).toBe("Verified species");
    expect(findCountLabel(1)).toBe("1 find");
    expect(findCountLabel(2)).toBe("2 finds");
  });

  test("rejects expired or malformed signed URLs", () => {
    jest.spyOn(Date, "now").mockReturnValue(Date.parse("2026-07-07T12:00:00Z"));

    expect(isUrlUsable("2026-07-07T12:01:00Z")).toBe(true);
    expect(isUrlUsable("2026-07-07T12:00:02Z")).toBe(false);
    expect(isUrlUsable("not-a-date")).toBe(false);

    jest.restoreAllMocks();
  });
});
