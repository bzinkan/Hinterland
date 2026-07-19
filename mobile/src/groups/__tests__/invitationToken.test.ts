import {
  captureInvitationTokenFromFragment,
  clearPendingInvitationToken,
  copyInvitationUrl,
  readPendingInvitationToken,
  validateInvitationUrl,
} from "@/src/groups/invitationToken";

const TOKEN = "A".repeat(48);

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>();
  get length() { return this.values.size; }
  clear() { this.values.clear(); }
  getItem(key: string) { return this.values.get(key) ?? null; }
  key(index: number) { return Array.from(this.values.keys())[index] ?? null; }
  removeItem(key: string) { this.values.delete(key); }
  setItem(key: string, value: string) { this.values.set(key, value); }
}

describe("group invitation tab-secret contract", () => {
  beforeEach(() => {
    jest.restoreAllMocks();
    const sessionStorage = new MemoryStorage();
    const localStorage = new MemoryStorage();
    const location = {
      origin: "https://parents.example",
      pathname: "/group-invite",
      search: "",
      hash: "",
    };
    const history = {
      replaceState: (_state: unknown, _title: string, value: string) => {
        const url = new URL(value, location.origin);
        location.pathname = url.pathname;
        location.search = url.search;
        location.hash = url.hash;
      },
    };
    const navigator = {};
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: { sessionStorage, localStorage, location, history, navigator },
    });
    Object.defineProperty(globalThis, "navigator", {
      configurable: true,
      value: navigator,
    });
    Object.defineProperty(globalThis, "document", {
      configurable: true,
      value: { title: "Invitation" },
    });
  });

  it("captures a valid fragment, immediately scrubs the address bar, and stores only in this tab", () => {
    window.history.replaceState(null, "", `/group-invite#token=${TOKEN}`);

    expect(captureInvitationTokenFromFragment()).toBe(TOKEN);
    expect(window.location.hash).toBe("");
    expect(window.location.pathname).toBe("/group-invite");
    expect(readPendingInvitationToken()).toBe(TOKEN);
    expect(window.localStorage.length).toBe(0);
  });

  it("expires the tab token after exactly the server's 72-hour lifetime", () => {
    const now = 1_800_000_000_000;
    jest.spyOn(Date, "now").mockReturnValue(now);
    window.history.replaceState(null, "", `/group-invite#token=${TOKEN}`);
    captureInvitationTokenFromFragment();

    jest.spyOn(Date, "now").mockReturnValue(now + 72 * 60 * 60 * 1000);
    expect(readPendingInvitationToken()).toBe(TOKEN);
    jest.spyOn(Date, "now").mockReturnValue(now + 72 * 60 * 60 * 1000 + 1);
    expect(readPendingInvitationToken()).toBeNull();
  });

  it("fails closed and clears malformed fragments and stored values", () => {
    window.history.replaceState(null, "", "/group-invite#token=short");
    expect(captureInvitationTokenFromFragment()).toBeNull();
    expect(window.location.hash).toBe("");

    window.sessionStorage.setItem(
      "hinterland.group_invitation.v1",
      JSON.stringify({ token: "visible", capturedAt: Date.now() }),
    );
    expect(readPendingInvitationToken()).toBeNull();
    expect(window.sessionStorage.length).toBe(0);
  });

  it("does not retain an older adult's invitation when tab storage rejects a new one", () => {
    window.sessionStorage.setItem(
      "hinterland.group_invitation.v1",
      JSON.stringify({ token: "B".repeat(48), capturedAt: Date.now() }),
    );
    jest.spyOn(window.sessionStorage, "setItem").mockImplementation(() => {
      throw new Error("storage unavailable");
    });
    window.history.replaceState(null, "", `/group-invite#token=${TOKEN}`);

    expect(captureInvitationTokenFromFragment()).toBeNull();
    expect(window.location.hash).toBe("");
    expect(window.sessionStorage.length).toBe(0);
    expect(readPendingInvitationToken()).toBeNull();
  });

  it("accepts only the canonical public /group-invite link before copying", async () => {
    const canonical = `https://parents.thehinterlandguide.app/group-invite#token=${TOKEN}`;
    expect(validateInvitationUrl(canonical)).toBe(true);
    expect(validateInvitationUrl(`${window.location.origin}/group-invite#token=${TOKEN}`)).toBe(false);
    expect(validateInvitationUrl(`https://evil.example/group-invite#token=${TOKEN}`)).toBe(false);
    expect(validateInvitationUrl(`https://parents.thehinterlandguide.app/groups#token=${TOKEN}`)).toBe(false);
    expect(validateInvitationUrl(`${canonical}&next=/groups`)).toBe(false);
    expect(
      validateInvitationUrl(
        `https://parents.thehinterlandguide.app/group-invite?next=/groups#token=${TOKEN}`,
      ),
    ).toBe(false);

    const writeText = jest.fn(async () => undefined);
    Object.defineProperty(window.navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    await copyInvitationUrl(canonical);
    expect(writeText).toHaveBeenCalledWith(canonical);
    await expect(copyInvitationUrl(`https://evil.example/group-invite#token=${TOKEN}`)).rejects.toThrow(
      "Invitation link is unavailable",
    );
  });

  it("clears the pending token explicitly after redemption or a kid boundary", () => {
    window.history.replaceState(null, "", `/group-invite#token=${TOKEN}`);
    captureInvitationTokenFromFragment();
    clearPendingInvitationToken();
    expect(readPendingInvitationToken()).toBeNull();
  });
});
