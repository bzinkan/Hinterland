import {
  consumeParentReturnPath,
  rememberParentReturnPath,
} from "@/src/auth/parentReturnPath";

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>();
  get length() { return this.values.size; }
  clear() { this.values.clear(); }
  getItem(key: string) { return this.values.get(key) ?? null; }
  key(index: number) { return Array.from(this.values.keys())[index] ?? null; }
  removeItem(key: string) { this.values.delete(key); }
  setItem(key: string, value: string) { this.values.set(key, value); }
}

describe("parent authentication return path", () => {
  beforeEach(() => {
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: { sessionStorage: new MemoryStorage() },
    });
  });

  it("preserves a group invitation only in the current tab across sign-in", () => {
    rememberParentReturnPath("/group-invite");
    expect(consumeParentReturnPath()).toBe("/group-invite");
    expect(consumeParentReturnPath()).toBe("/groups");
  });

  it("fails closed to Groups for an unrecognized stored path", () => {
    window.sessionStorage.setItem(
      "hinterland.parent_return_path.v1",
      "https://evil.example/steal",
    );
    expect(consumeParentReturnPath()).toBe("/groups");
    expect(window.sessionStorage.length).toBe(0);
  });
});
