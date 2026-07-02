import { encodeGeohash4 } from "@/src/expeditions/geohash";

const ALPHABET = "0123456789bcdefghjkmnpqrstuvwxyz";

describe("encodeGeohash4", () => {
  it("matches the classic u4pruydqqvj vector truncated to 4 chars", () => {
    expect(encodeGeohash4(57.64911, 10.40744)).toBe("u4pr");
  });

  it("encodes the origin as s000", () => {
    expect(encodeGeohash4(0, 0)).toBe("s000");
  });

  it("encodes Sydney (southern/eastern hemisphere) as r3gx", () => {
    // Verified by an independent manual bit-walk of the interleaving
    // algorithm; also the prefix of the published Sydney hash r3gx2f.
    expect(encodeGeohash4(-33.8688, 151.2093)).toBe("r3gx");
  });

  it("encodes New York (northern/western hemisphere) as dr5r", () => {
    // Verified by manual bit-walk; prefix of the published dr5regw.
    expect(encodeGeohash4(40.7128, -74.006)).toBe("dr5r");
  });

  it.each([
    [90, 180],
    [90, -180],
    [-90, 180],
    [-90, -180],
  ])("produces 4 valid alphabet chars at lat %p / lng %p", (lat, lng) => {
    const hash = encodeGeohash4(lat, lng);
    expect(hash).toHaveLength(4);
    for (const ch of hash) {
      expect(ALPHABET).toContain(ch);
    }
  });

  it("clamps the extreme corners to the expected cells", () => {
    // lat 90 / lng 180: every interleaved bit is 1 -> all-z hash.
    expect(encodeGeohash4(90, 180)).toBe("zzzz");
    // lat -90 / lng -180: every interleaved bit is 0 -> all-0 hash.
    expect(encodeGeohash4(-90, -180)).toBe("0000");
    // Out-of-range inputs clamp to the same corner cells.
    expect(encodeGeohash4(91, 181)).toBe("zzzz");
    expect(encodeGeohash4(-91, -181)).toBe("0000");
  });
});
