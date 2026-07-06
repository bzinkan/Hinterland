import {
  SANCTUARY_PALETTE_SLOTS,
  type SanctuaryPaletteSlot,
} from "@/src/sanctuary/art/islandLayers.gen";
import {
  createSvgCache,
  substitute,
  SVG_CACHE_CAPACITY,
  type SlotHexes,
} from "@/src/sanctuary/dioramaui/svgCache";

function slots(overrides: Partial<SlotHexes> = {}): SlotHexes {
  const base = Object.fromEntries(
    SANCTUARY_PALETTE_SLOTS.map((slot, i) => [
      slot,
      `#${i.toString(16).padStart(6, "0")}`,
    ]),
  ) as SlotHexes;
  return { ...base, ...overrides };
}

describe("substitute", () => {
  it("replaces every occurrence of every slot token", () => {
    const svg =
      '<path fill="{{green_mid}}"/><path fill="{{green_mid}}"/>' +
      '<stop stop-color="{{horizon}}"/>';
    const out = substitute(svg, slots({ green_mid: "#AABBCC", horizon: "#112233" }));
    expect(out).toBe(
      '<path fill="#AABBCC"/><path fill="#AABBCC"/>' +
        '<stop stop-color="#112233"/>',
    );
  });

  it("handles all 12 slots", () => {
    const svg = SANCTUARY_PALETTE_SLOTS.map((s) => `{{${s}}}`).join(",");
    const out = substitute(svg, slots());
    expect(out).not.toContain("{{");
    expect(out.split(",")).toHaveLength(SANCTUARY_PALETTE_SLOTS.length);
  });

  it("throws in dev when an unknown token survives", () => {
    expect(() => substitute('<path fill="{{lava}}"/>', slots())).toThrow(
      /\{\{lava\}\}/,
    );
  });

  it("leaves token-free svg untouched", () => {
    const svg = '<svg viewBox="0 0 512 384"><path fill="#123456"/></svg>';
    expect(substitute(svg, slots())).toBe(svg);
  });
});

describe("createSvgCache", () => {
  const asSlot = (hex: string): Partial<SlotHexes> => ({ green_mid: hex });

  it("parses once per (key, palette) and serves the cached object after", () => {
    const parse = jest.fn((svg: string) => ({ svg }));
    const cache = createSvgCache(parse);
    const a1 = cache.makeSvg("layer:meadow:mid", "{{green_mid}}", slots());
    const a2 = cache.makeSvg("layer:meadow:mid", "{{green_mid}}", slots());
    expect(parse).toHaveBeenCalledTimes(1);
    expect(a2).toBe(a1);
    expect(cache.size()).toBe(1);
  });

  it("re-parses when the palette hexes change (season remap)", () => {
    const parse = jest.fn((svg: string) => ({ svg }));
    const cache = createSvgCache(parse);
    cache.makeSvg("k", "{{green_mid}}", slots(asSlot("#111111")));
    cache.makeSvg("k", "{{green_mid}}", slots(asSlot("#222222")));
    expect(parse).toHaveBeenCalledTimes(2);
    expect(parse).toHaveBeenNthCalledWith(1, "#111111");
    expect(parse).toHaveBeenNthCalledWith(2, "#222222");
  });

  it("caches null parses instead of retrying", () => {
    const parse = jest.fn(() => null);
    const cache = createSvgCache(parse);
    expect(cache.makeSvg("bad", "x", slots())).toBeNull();
    expect(cache.makeSvg("bad", "x", slots())).toBeNull();
    expect(parse).toHaveBeenCalledTimes(1);
  });

  it("evicts the least-recently-used entry past capacity", () => {
    const parse = jest.fn((svg: string) => ({ svg }));
    const cache = createSvgCache(parse, 2);
    cache.makeSvg("a", "a", slots());
    cache.makeSvg("b", "b", slots());
    // Touch "a" so "b" is now least recently used.
    cache.makeSvg("a", "a", slots());
    cache.makeSvg("c", "c", slots());
    expect(cache.size()).toBe(2);
    expect(parse).toHaveBeenCalledTimes(3);
    // "a" survived; "b" was evicted and re-parses.
    cache.makeSvg("a", "a", slots());
    expect(parse).toHaveBeenCalledTimes(3);
    cache.makeSvg("b", "b", slots());
    expect(parse).toHaveBeenCalledTimes(4);
  });

  it("exports a capacity that comfortably fits the spike's asset set", () => {
    expect(SVG_CACHE_CAPACITY).toBeGreaterThanOrEqual(64);
  });
});

// Keep the union in sync with usage above: a compile-time check that the
// helper's overrides really are palette slots.
const _slotCheck: SanctuaryPaletteSlot = "green_mid";
void _slotCheck;
