import renderer, { act } from "react-test-renderer";

import GroupInviteScreen from "@/app/group-invite";
import { useAuthSession } from "@/src/auth/session";

const mockCapture = jest.fn(() => "A".repeat(48));
const mockRead = jest.fn(() => "A".repeat(48));
const mockClear = jest.fn();
const mockRememberReturn = jest.fn();
const mockMutate = jest.fn();
const mockReset = jest.fn();
const mockInvalidate = jest.fn();
const mockSignIn = jest.fn();

jest.mock("expo-router", () => ({
  router: { push: jest.fn(), replace: jest.fn() },
  Stack: { Screen: () => null },
}));

const mockedRouter = jest.requireMock("expo-router").router as {
  push: jest.Mock;
  replace: jest.Mock;
};

jest.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: mockInvalidate }),
  useMutation: () => ({
    mutate: mockMutate,
    reset: mockReset,
    isPending: false,
    error: null,
  }),
}));

jest.mock("@/src/api/groups", () => ({ redeemAdultInvitation: jest.fn() }));
jest.mock("@/src/auth/msal", () => ({ signIn: () => mockSignIn() }));
jest.mock("@/src/auth/parentReturnPath", () => ({
  rememberParentReturnPath: (path: string) => mockRememberReturn(path),
}));
jest.mock("@/src/groups/invitationToken", () => ({
  captureInvitationTokenFromFragment: () => mockCapture(),
  readPendingInvitationToken: () => mockRead(),
  clearPendingInvitationToken: () => mockClear(),
}));

describe("GroupInviteScreen privacy boundary", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockCapture.mockReturnValue("A".repeat(48));
    mockRead.mockReturnValue("A".repeat(48));
  });

  it("keeps the tab invitation across sign-in and returns for explicit confirmation", () => {
    useAuthSession.getState().setAnonymous();
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupInviteScreen />);
    });

    act(() => tree.root.findByProps({ testID: "groups-invite-sign-in" }).props.onPress());
    expect(mockRememberReturn).toHaveBeenCalledWith("/group-invite");
    expect(mockedRouter.push).toHaveBeenCalledWith("/sign-in");
    expect(mockClear).not.toHaveBeenCalled();
    expect(mockMutate).not.toHaveBeenCalled();
    act(() => tree.unmount());
  });

  it("never redeems automatically when the authenticated adult changes", () => {
    useAuthSession.getState().setAuthenticated({
      id: "parent-1",
      entra_oid: "entra-1",
      role: "parent",
      display_name: "Parent One",
    });
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupInviteScreen />);
    });
    expect(mockMutate).not.toHaveBeenCalled();

    act(() => {
      useAuthSession.getState().setAuthenticated({
        id: "parent-2",
        entra_oid: "entra-2",
        role: "parent",
        display_name: "Parent Two",
      });
    });
    expect(JSON.stringify(tree.toJSON())).toContain("Parent Two");
    expect(mockMutate).not.toHaveBeenCalled();
    expect(mockClear).not.toHaveBeenCalled();

    act(() => tree.root.findByProps({ testID: "groups-invite-confirm" }).props.onPress());
    expect(mockMutate).toHaveBeenCalledWith("A".repeat(48));
    act(() => tree.unmount());
  });

  it("clears an invitation instead of exposing it to a kid session", () => {
    useAuthSession.getState().setAuthenticated({
      id: "kid-1",
      entra_oid: null,
      role: "kid",
      display_name: "Kid",
    });
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupInviteScreen />);
    });
    expect(mockClear).toHaveBeenCalled();
    expect(JSON.stringify(tree.toJSON())).toContain("Group invitations are for parents and guardians");
    expect(mockMutate).not.toHaveBeenCalled();
    act(() => tree.unmount());
  });
});
