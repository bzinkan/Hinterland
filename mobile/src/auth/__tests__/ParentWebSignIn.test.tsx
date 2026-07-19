import { act, create, type ReactTestRenderer } from "react-test-renderer";

import { ParentWebSignIn, safeParentSetupError } from "@/app/sign-in";
import { ApiError } from "@/src/api/client";
import { useAuthSession } from "@/src/auth/session";

const mockParentSignup = jest.fn();
const mockGetSignedInAdultProfile = jest.fn();
const mockRefreshCurrentAdultSession = jest.fn();
const mockSignIn = jest.fn();
const mockPendingConsentProof = {
  receiptId: "consent-1",
  nonce: "ab".repeat(32),
  policyVersion: "2026-07-11-W1-INTERNAL",
};
let mockStoredConsentProof: typeof mockPendingConsentProof | null = mockPendingConsentProof;
const mockClearConsentProof = jest.fn((_proof: typeof mockPendingConsentProof) => {
  mockStoredConsentProof = null;
});
jest.mock("expo-router", () => ({
  router: { push: jest.fn(), replace: jest.fn() },
  Stack: { Screen: () => null },
}));

const mockedRouter = jest.requireMock("expo-router").router as {
  push: jest.Mock;
  replace: jest.Mock;
};

jest.mock("@/src/api/auth", () => ({
  parentSignup: (displayName: string, proof: typeof mockPendingConsentProof) =>
    mockParentSignup(displayName, proof),
}));

jest.mock("@/src/auth/consentProof", () => ({
  ...jest.requireActual("@/src/auth/consentProof"),
  readPendingParentConsentProof: () => mockStoredConsentProof,
  clearPendingParentConsentProof: (proof: typeof mockPendingConsentProof) =>
    mockClearConsentProof(proof),
}));

jest.mock("@/src/auth/msal", () => ({
  getSignedInAdultProfile: () => mockGetSignedInAdultProfile(),
  refreshCurrentAdultSession: () => mockRefreshCurrentAdultSession(),
  signIn: () => mockSignIn(),
}));

async function renderReady(): Promise<ReactTestRenderer> {
  let tree!: ReactTestRenderer;
  await act(async () => {
    tree = create(<ParentWebSignIn />);
    await Promise.resolve();
    await Promise.resolve();
  });
  return tree;
}

describe("ParentWebSignIn", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockStoredConsentProof = mockPendingConsentProof;
    useAuthSession.getState().setAnonymous();
    mockGetSignedInAdultProfile.mockResolvedValue({
      suggestedDisplayName: "Alex Adult",
    });
    mockParentSignup.mockResolvedValue({
      id: "parent-1",
      entra_oid: "entra-1",
      role: "parent",
      display_name: "Alex Adult",
    });
    mockRefreshCurrentAdultSession.mockResolvedValue(undefined);
  });

  it("explicitly confirms the adult name before signup and refreshes canonical identity", async () => {
    const tree = await renderReady();

    expect(tree.root.findByProps({ testID: "parent-display-name" }).props.value).toBe(
      "Alex Adult",
    );
    await act(async () => {
      tree.root.findByProps({ testID: "finish-parent-setup" }).props.onPress();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockParentSignup).toHaveBeenCalledWith(
      "Alex Adult",
      mockPendingConsentProof,
    );
    expect(mockClearConsentProof).toHaveBeenCalledWith(mockPendingConsentProof);
    expect(mockRefreshCurrentAdultSession).toHaveBeenCalledTimes(1);
    expect(mockParentSignup.mock.invocationCallOrder[0]).toBeLessThan(
      mockRefreshCurrentAdultSession.mock.invocationCallOrder[0],
    );

    await act(async () => {
      useAuthSession.getState().setAuthenticated({
        id: "parent-1",
        entra_oid: "entra-1",
        role: "parent",
        display_name: "Alex Adult",
      });
      await Promise.resolve();
    });
    expect(mockedRouter.replace).toHaveBeenCalledWith("/groups");

    act(() => tree.unmount());
  });

  it("allows fresh Microsoft sign-in when the tab proof is present", async () => {
    mockGetSignedInAdultProfile.mockResolvedValue(null);
    const tree = await renderReady();

    await act(async () => {
      tree.root.findByProps({ testID: "parent-msal-sign-in" }).props.onPress();
      await Promise.resolve();
    });

    expect(mockSignIn).toHaveBeenCalledTimes(1);
    expect(mockParentSignup).not.toHaveBeenCalled();
    act(() => tree.unmount());
  });

  it("maps stale-consent conflicts to guidance without exposing raw API text", async () => {
    mockParentSignup.mockRejectedValue(
      new ApiError(
        409,
        {
          error: {
            code: "consent_required",
            message: "raw server policy details must not render",
            request_id: "request-safe-123",
          },
        },
        "raw server policy details must not render",
      ),
    );
    const tree = await renderReady();

    await act(async () => {
      tree.root.findByProps({ testID: "finish-parent-setup" }).props.onPress();
      await Promise.resolve();
      await Promise.resolve();
    });

    const rendered = JSON.stringify(tree.toJSON());
    expect(rendered).toContain("Review the current pilot consent");
    expect(rendered).toContain("request-safe-123");
    expect(rendered).not.toContain("raw server policy details must not render");
    act(() => {
      tree.root
        .findByProps({ accessibilityLabel: "Review current pilot consent" })
        .props.onPress();
    });
    expect(mockedRouter.push).toHaveBeenCalledWith("/consent");

    act(() => tree.unmount());
  });

  it("sends an already-resolved adult with no pending consent directly to Groups", async () => {
    mockStoredConsentProof = null;
    useAuthSession.getState().setAuthenticated({
      id: "existing-parent",
      entra_oid: "entra-existing",
      role: "parent",
      display_name: "Existing Parent",
    });
    let tree!: ReactTestRenderer;
    await act(async () => {
      tree = create(<ParentWebSignIn />);
      await Promise.resolve();
    });

    expect(mockedRouter.replace).toHaveBeenCalledWith("/groups");
    expect(mockParentSignup).not.toHaveBeenCalled();
    expect(mockGetSignedInAdultProfile).not.toHaveBeenCalled();
    act(() => tree.unmount());
  });

  it("links a pending re-consent before redirecting an existing adult", async () => {
    useAuthSession.getState().setAuthenticated({
      id: "existing-parent",
      entra_oid: "entra-existing",
      role: "parent",
      display_name: "Existing Parent",
    });
    let tree!: ReactTestRenderer;
    await act(async () => {
      tree = create(<ParentWebSignIn />);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockParentSignup).toHaveBeenCalledWith(
      "Existing Parent",
      mockPendingConsentProof,
    );
    expect(mockClearConsentProof).toHaveBeenCalledWith(mockPendingConsentProof);
    expect(mockedRouter.replace).toHaveBeenCalledWith("/groups");

    act(() => tree.unmount());
  });

  it("lets a signed-out returning parent sign in without recording consent again", async () => {
    mockStoredConsentProof = null;
    mockGetSignedInAdultProfile.mockResolvedValue(null);
    const tree = await renderReady();

    const rendered = JSON.stringify(tree.toJSON());
    expect(rendered).toContain("Already have a parent account");
    await act(async () => {
      tree.root.findByProps({ testID: "parent-msal-sign-in" }).props.onPress();
      await Promise.resolve();
    });

    expect(mockSignIn).toHaveBeenCalledTimes(1);
    expect(mockParentSignup).not.toHaveBeenCalled();
    act(() => tree.unmount());
  });

  it("fails closed before fresh parent setup when the tab proof is missing", async () => {
    mockStoredConsentProof = null;
    const tree = await renderReady();

    const rendered = JSON.stringify(tree.toJSON());
    expect(rendered).toContain("not linked to a Hinterland parent account");
    expect(rendered).toContain("Review current pilot consent");
    expect(rendered).not.toContain("Finish parent setup");
    expect(mockParentSignup).not.toHaveBeenCalled();
    expect(mockSignIn).not.toHaveBeenCalled();

    act(() => tree.unmount());
  });
});

describe("safeParentSetupError", () => {
  it("never returns arbitrary exception text", () => {
    expect(safeParentSetupError(new Error("sensitive provider detail"))).toEqual({
      message:
        "We couldn't finish parent setup. No child account was created. Please try again.",
      needsCurrentConsent: false,
      supportCode: null,
    });
  });
});
