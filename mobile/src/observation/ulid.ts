import * as Crypto from "expo-crypto";

const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

/** Generate a canonical 26-character ULID for one observation submission. */
export async function createSubmissionUlid(now = Date.now()): Promise<string> {
  if (!Number.isSafeInteger(now) || now < 0 || now > 281_474_976_710_655) {
    throw new Error("ULID timestamp is out of range");
  }
  const randomness = await Crypto.getRandomBytesAsync(10);
  return encodeTime(now) + encodeRandomness(randomness);
}

function encodeTime(timestamp: number): string {
  let remaining = timestamp;
  const chars = new Array<string>(10);
  for (let index = 9; index >= 0; index -= 1) {
    chars[index] = CROCKFORD[remaining % 32];
    remaining = Math.floor(remaining / 32);
  }
  return chars.join("");
}

function encodeRandomness(bytes: Uint8Array): string {
  if (bytes.length !== 10) throw new Error("ULID randomness must be 80 bits");
  let bits = 0;
  let bitCount = 0;
  let output = "";

  for (const byte of bytes) {
    bits = (bits << 8) | byte;
    bitCount += 8;
    while (bitCount >= 5) {
      bitCount -= 5;
      output += CROCKFORD[(bits >>> bitCount) & 31];
      // Keep only the unconsumed tail so bitwise operations stay bounded.
      bits &= (1 << bitCount) - 1;
    }
  }
  if (bitCount > 0) output += CROCKFORD[(bits << (5 - bitCount)) & 31];
  return output;
}
