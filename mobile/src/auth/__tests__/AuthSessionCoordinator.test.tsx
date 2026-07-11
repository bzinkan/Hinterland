import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";

import { CurrentUserContractError } from "@/src/api/auth";
import { AuthSessionCoordinator } from "@/src/auth/AuthSessionCoordinator";
import { useAuthSession } from "@/src/auth/session";

const mockGetMe = jest.fn();
const mockGetBearerToken = jest.fn();
const mockGetPersistedSessionUser = jest.fn();
const mockClearPersistedSessionUser = jest.fn(async () => undefined);
const mockPersistSessionUser = jest.fn(async (_user: unknown, _token: string) => undefined);
const mockSetObservationQueueOwner = jest.fn();
const mockClearDraft = jest.fn();

jest.mock("@/src/api/auth", () => {
  const actual = jest.requireActual("@/src/api/auth");
  return { ...actual, getMe: (...args: unknown[]) => mockGetMe(...args) };
});

jest.mock("@/src/auth/token", () => ({
  getBearerToken: () => mockGetBearerToken(),
  subscribeBearerTokenChanges: jest.fn(() => jest.fn()),
}));

jest.mock("@/src/auth/session", () => {
  const actual = jest.requireActual("@/src/auth/session");
  return {
    ...actual,
    clearPersistedSessionUser: () => mockClearPersistedSessionUser(),
    getPersistedSessionUser: (...args: unknown[]) => mockGetPersistedSessionUser(...args),
    persistSessionUser: (user: unknown, token: string) => mockPersistSessionUser(user, token),
  };
});

jest.mock("@/src/auth/requestBoundary", () => ({
  rotateImperativeRequestBoundary: jest.fn(),
}));

jest.mock("@/src/observation/queueSync", () => ({
  resumeOwnerObservationQueue: jest.fn(async () => []),
  setObservationQueueOwner: (...args: unknown[]) => mockSetObservationQueueOwner(...args),
}));

jest.mock("@/src/observation/draftStore", () => ({
  useDraftStore: { getState: () => ({ clear: mockClearDraft }) },
}));

describe("AuthSessionCoordinator", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockGetBearerToken.mockResolvedValue("kid-session-token");
    useAuthSession.getState().setAuthenticated({
      id: "cached-kid",
      entra_oid: null,
      role: "kid",
      display_name: "Cached Kid",
    });
  });

  it("clears a persisted owner instead of restoring it after a malformed /v1/me 200", async () => {
    mockGetMe.mockRejectedValue(new CurrentUserContractError());
    mockGetPersistedSessionUser.mockResolvedValue({
      id: "cached-kid",
      entra_oid: null,
      role: "kid",
      display_name: "Cached Kid",
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let renderer: ReactTestRenderer | undefined;

    await act(async () => {
      renderer = create(
        <QueryClientProvider client={queryClient}>
          <AuthSessionCoordinator />
        </QueryClientProvider>,
      );
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockGetPersistedSessionUser).not.toHaveBeenCalled();
    expect(mockClearPersistedSessionUser).toHaveBeenCalled();
    expect(mockSetObservationQueueOwner).not.toHaveBeenCalledWith("cached-kid");
    expect(useAuthSession.getState().status).toBe("anonymous");

    await act(async () => renderer?.unmount());
    queryClient.clear();
  });
});
