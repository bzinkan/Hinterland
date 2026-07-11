import type {
  AccountInfo,
  PublicClientApplication,
} from "@azure/msal-browser";

type MsalAccountClient = Pick<
  PublicClientApplication,
  | "acquireTokenSilent"
  | "getActiveAccount"
  | "getAllAccounts"
  | "setActiveAccount"
>;

export type MsalAuthEventTypes = {
  LOGIN_SUCCESS: string;
  ACTIVE_ACCOUNT_CHANGED: string;
  LOGOUT_SUCCESS: string;
};

export type MsalTokenStore = {
  get: () => Promise<string | null>;
  set: (token: string) => Promise<void>;
  clear: () => Promise<void>;
};

type CachedAccountSelection = {
  account: AccountInfo | null;
  epoch: number;
};

/**
 * Owns MSAL's active-account decision and guards bearer publication with an
 * epoch. Keeping this state together makes delayed silent-acquisition results
 * harmless after logout or an adult-account switch.
 */
export class MsalSessionController {
  private authEpoch = 0;

  constructor(
    private readonly ms: MsalAccountClient,
    private readonly tokenStore: MsalTokenStore,
    private readonly scopes: string[],
  ) {}

  activateAccount(account: AccountInfo): void {
    this.transitionToAccount(account);
  }

  async syncCachedAccount(): Promise<void> {
    const selection = this.selectCachedAccount();
    await this.syncToken(selection.account, selection.epoch);
  }

  async acquireCurrentAccount(
    forcePublish: boolean,
  ): Promise<AccountInfo | null> {
    const selection = this.selectCachedAccount();
    const account = selection.account;
    if (!account) {
      await this.syncToken(null, selection.epoch);
      return null;
    }

    try {
      const published = await this.acquireAndStoreToken(
        account,
        forcePublish,
        selection.epoch,
      );
      if (!published) {
        throw new Error(
          "Microsoft sign-in changed while the session was loading.",
        );
      }
      return account;
    } catch (error) {
      if (this.tokenPublicationIsCurrent(account, selection.epoch)) {
        await this.tokenStore.clear();
      }
      throw error;
    }
  }

  async beginLogout(): Promise<AccountInfo | null> {
    const account = this.ms.getActiveAccount();
    const epoch = this.transitionToAccount(null);
    await this.syncToken(null, epoch);
    return account;
  }

  async handleEvent(
    eventType: string,
    payload: unknown,
    eventTypes: MsalAuthEventTypes,
  ): Promise<void> {
    // Acquire-token events are deliberately absent: reacting to acquisition
    // success by acquiring again creates a feedback loop.
    if (eventType === eventTypes.LOGIN_SUCCESS) {
      const account = accountFromLoginPayload(payload);
      const epoch = this.transitionToAccount(account);
      await this.syncToken(account, epoch);
      return;
    }

    if (eventType === eventTypes.LOGOUT_SUCCESS) {
      const epoch = this.transitionToAccount(null);
      await this.syncToken(null, epoch);
      return;
    }

    if (eventType === eventTypes.ACTIVE_ACCOUNT_CHANGED) {
      // The active selection is authoritative. Never substitute the first
      // account in MSAL's cache when there is no active selection.
      const account = this.ms.getActiveAccount();
      const epoch = this.transitionToAccount(account);
      await this.syncToken(account, epoch);
    }
  }

  private async acquireAndStoreToken(
    account: AccountInfo,
    forcePublish: boolean,
    expectedEpoch: number,
  ): Promise<boolean> {
    const result = await this.ms.acquireTokenSilent({
      account,
      scopes: this.scopes,
    });
    if (!this.tokenPublicationIsCurrent(account, expectedEpoch)) return false;

    const storedToken = await this.tokenStore.get();
    if (!this.tokenPublicationIsCurrent(account, expectedEpoch)) return false;
    if (forcePublish || storedToken !== result.accessToken) {
      await this.tokenStore.set(result.accessToken);
      if (!this.tokenPublicationIsCurrent(account, expectedEpoch)) return false;
    }
    return true;
  }

  private async syncToken(
    account: AccountInfo | null,
    expectedEpoch: number,
  ): Promise<void> {
    if (!account) {
      if (expectedEpoch === this.authEpoch) await this.tokenStore.clear();
      return;
    }

    try {
      await this.acquireAndStoreToken(account, false, expectedEpoch);
    } catch {
      // An obsolete account's failure must not clear a newer account's token.
      if (this.tokenPublicationIsCurrent(account, expectedEpoch)) {
        await this.tokenStore.clear();
      }
    }
  }

  private tokenPublicationIsCurrent(
    account: AccountInfo,
    expectedEpoch: number,
  ): boolean {
    return (
      expectedEpoch === this.authEpoch &&
      sameAccount(this.ms.getActiveAccount(), account)
    );
  }

  private transitionToAccount(account: AccountInfo | null): number {
    this.authEpoch += 1;
    if (!sameAccount(this.ms.getActiveAccount(), account)) {
      this.ms.setActiveAccount(account);
    }
    return this.authEpoch;
  }

  /**
   * MSAL does not choose an active account automatically when multiple cached
   * adults exist. A single cached account is safe to activate for refresh;
   * multiple accounts without an active selection fail closed.
   */
  private selectCachedAccount(): CachedAccountSelection {
    const active = this.ms.getActiveAccount();
    if (active) return { account: active, epoch: this.authEpoch };

    const cached = this.ms.getAllAccounts();
    if (cached.length !== 1) {
      return {
        account: null,
        epoch: this.transitionToAccount(null),
      };
    }

    const [soleAccount] = cached;
    return {
      account: soleAccount,
      epoch: this.transitionToAccount(soleAccount),
    };
  }
}

function accountKey(account: AccountInfo | null): string | null {
  if (!account) return null;
  return account.homeAccountId || account.localAccountId || account.username;
}

function sameAccount(
  left: AccountInfo | null,
  right: AccountInfo | null,
): boolean {
  return accountKey(left) === accountKey(right);
}

function accountFromLoginPayload(payload: unknown): AccountInfo | null {
  if (!payload || typeof payload !== "object" || !("account" in payload)) {
    return null;
  }
  return (payload as { account?: AccountInfo | null }).account ?? null;
}
