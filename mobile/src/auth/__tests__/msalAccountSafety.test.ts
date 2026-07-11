import type {
  AccountInfo,
  PublicClientApplication,
} from "@azure/msal-browser";

import {
  MsalSessionController,
  type MsalAuthEventTypes,
} from "@/src/auth/msalSession";

const eventTypes: MsalAuthEventTypes = {
  LOGIN_SUCCESS: "msal:loginSuccess",
  ACTIVE_ACCOUNT_CHANGED: "msal:activeAccountChanged",
  LOGOUT_SUCCESS: "msal:logoutSuccess",
};

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
};

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function account(id: string, name = `Adult ${id}`): AccountInfo {
  return {
    homeAccountId: `home-${id}`,
    environment: "login.example.test",
    tenantId: "tenant",
    username: `${id}@example.test`,
    localAccountId: `local-${id}`,
    name,
  } as AccountInfo;
}

class FakeMsalClient {
  activeAccount: AccountInfo | null;
  cachedAccounts: AccountInfo[];

  acquireTokenSilent = jest.fn(
    async ({ account: requested }: { account: AccountInfo }) => ({
      account: requested,
      accessToken: `token-${requested.localAccountId}`,
    }),
  );
  getActiveAccount = jest.fn(() => this.activeAccount);
  getAllAccounts = jest.fn(() => this.cachedAccounts);
  setActiveAccount = jest.fn((selected: AccountInfo | null) => {
    this.activeAccount = selected;
  });

  constructor(accounts: AccountInfo[], active: AccountInfo | null) {
    this.cachedAccounts = accounts;
    this.activeAccount = active;
  }
}

function setup(accounts: AccountInfo[], active: AccountInfo | null) {
  const ms = new FakeMsalClient(accounts, active);
  const tokenStore = {
    get: jest.fn<Promise<string | null>, []>().mockResolvedValue(null),
    set: jest.fn<Promise<void>, [string]>().mockResolvedValue(undefined),
    clear: jest.fn<Promise<void>, []>().mockResolvedValue(undefined),
  };
  const controller = new MsalSessionController(
    ms as unknown as Pick<
      PublicClientApplication,
      | "acquireTokenSilent"
      | "getActiveAccount"
      | "getAllAccounts"
      | "setActiveAccount"
    >,
    tokenStore,
    ["api://hinterland-api/user.access"],
  );
  return { controller, ms, tokenStore };
}

describe("MSAL active-account and token-publication safety", () => {
  it("makes redirect account B active instead of reusing cached account A", async () => {
    const adultA = account("a");
    const adultB = account("b");
    const { controller, ms, tokenStore } = setup(
      [adultA, adultB],
      adultA,
    );

    controller.activateAccount(adultB);
    await controller.syncCachedAccount();

    expect(ms.setActiveAccount).toHaveBeenCalledWith(adultB);
    expect(ms.acquireTokenSilent).toHaveBeenCalledTimes(1);
    expect(ms.acquireTokenSilent).toHaveBeenCalledWith(
      expect.objectContaining({ account: adultB }),
    );
    expect(tokenStore.set).toHaveBeenCalledWith("token-local-b");
  });

  it("makes a login-success account active before publishing its token", async () => {
    const adultA = account("a");
    const adultB = account("b");
    const { controller, ms, tokenStore } = setup(
      [adultA, adultB],
      adultA,
    );

    await controller.handleEvent(
      eventTypes.LOGIN_SUCCESS,
      { account: adultB },
      eventTypes,
    );

    expect(ms.setActiveAccount).toHaveBeenCalledWith(adultB);
    expect(tokenStore.set).toHaveBeenCalledWith("token-local-b");
  });

  it("fails closed when multiple cached accounts have no active selection", async () => {
    const adultA = account("a");
    const adultB = account("b");
    const { controller, ms, tokenStore } = setup(
      [adultA, adultB],
      null,
    );

    await controller.syncCachedAccount();

    expect(ms.acquireTokenSilent).not.toHaveBeenCalled();
    expect(tokenStore.set).not.toHaveBeenCalled();
    expect(tokenStore.clear).toHaveBeenCalledTimes(1);
    expect(ms.getActiveAccount()).toBeNull();
  });

  it("does not publish a delayed token after logout begins", async () => {
    const adultA = account("a");
    const delayedA = deferred<{ account: AccountInfo; accessToken: string }>();
    const { controller, ms, tokenStore } = setup([adultA], adultA);
    ms.acquireTokenSilent.mockImplementationOnce(
      async () => await delayedA.promise,
    );

    const inFlight = controller.syncCachedAccount();
    expect(ms.acquireTokenSilent).toHaveBeenCalledTimes(1);
    const logoutAccount = await controller.beginLogout();
    delayedA.resolve({ account: adultA, accessToken: "late-token-a" });
    await inFlight;

    expect(logoutAccount).toBe(adultA);
    expect(ms.setActiveAccount).toHaveBeenCalledWith(null);
    expect(tokenStore.clear).toHaveBeenCalledTimes(1);
    expect(tokenStore.set).not.toHaveBeenCalled();
  });

  it("lets account B win when account A acquisition resolves after a switch", async () => {
    const adultA = account("a");
    const adultB = account("b");
    const delayedA = deferred<{ account: AccountInfo; accessToken: string }>();
    const { controller, ms, tokenStore } = setup(
      [adultA, adultB],
      adultA,
    );
    ms.acquireTokenSilent.mockImplementation(
      async ({ account: requested }: { account: AccountInfo }) => {
        if (requested.homeAccountId === adultA.homeAccountId) {
          return await delayedA.promise;
        }
        return { account: requested, accessToken: "token-b-after-switch" };
      },
    );

    const inFlightA = controller.syncCachedAccount();
    expect(ms.acquireTokenSilent).toHaveBeenCalledTimes(1);

    ms.activeAccount = adultB;
    await controller.handleEvent(
      eventTypes.ACTIVE_ACCOUNT_CHANGED,
      null,
      eventTypes,
    );
    expect(tokenStore.set).toHaveBeenCalledWith("token-b-after-switch");

    delayedA.resolve({ account: adultA, accessToken: "late-token-a" });
    await inFlightA;
    expect(tokenStore.set).toHaveBeenCalledTimes(1);
    expect(tokenStore.set).not.toHaveBeenCalledWith("late-token-a");
  });

  it("does not let an obsolete acquisition failure clear account B", async () => {
    const adultA = account("a");
    const adultB = account("b");
    let rejectA!: (error: Error) => void;
    const delayedFailureA = new Promise<never>((_resolve, reject) => {
      rejectA = reject;
    });
    const { controller, ms, tokenStore } = setup(
      [adultA, adultB],
      adultA,
    );
    ms.acquireTokenSilent.mockImplementation(
      async ({ account: requested }: { account: AccountInfo }) => {
        if (requested.homeAccountId === adultA.homeAccountId) {
          return await delayedFailureA;
        }
        return { account: requested, accessToken: "token-b-after-switch" };
      },
    );

    const inFlightA = controller.syncCachedAccount();
    ms.activeAccount = adultB;
    await controller.handleEvent(
      eventTypes.ACTIVE_ACCOUNT_CHANGED,
      null,
      eventTypes,
    );
    rejectA(new Error("late account A failure"));
    await inFlightA;

    expect(tokenStore.set).toHaveBeenCalledWith("token-b-after-switch");
    expect(tokenStore.clear).not.toHaveBeenCalled();
  });

  it("ignores acquire-token events to avoid feedback loops", async () => {
    const adultA = account("a");
    const { controller, ms } = setup([adultA], adultA);

    await controller.syncCachedAccount();
    expect(ms.acquireTokenSilent).toHaveBeenCalledTimes(1);

    await controller.handleEvent(
      "msal:acquireTokenSuccess",
      { account: adultA },
      eventTypes,
    );
    expect(ms.acquireTokenSilent).toHaveBeenCalledTimes(1);
  });
});
