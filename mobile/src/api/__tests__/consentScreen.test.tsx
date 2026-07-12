import { StyleSheet, TextInput } from "react-native";
import renderer, { act } from "react-test-renderer";

import ConsentScreen from "@/app/consent";
import { useAuthSession } from "@/src/auth/session";

const mockNonce = "ab".repeat(32);
const mockProof = {
  receiptId: "consent-receipt-1",
  nonce: mockNonce,
  policyVersion: "2026-07-11-W1-INTERNAL",
};
let mockStoredProof: typeof mockProof | null = null;
const mockRecordConsent = jest.fn();
const mockParentSignup = jest.fn();
const mockStoreProof = jest.fn((value: typeof mockProof) => {
  mockStoredProof = value;
});
const mockClearProof = jest.fn((_value: typeof mockProof) => {
  mockStoredProof = null;
});

jest.mock("expo-router", () => ({
  router: { push: jest.fn(), replace: jest.fn() },
}));

jest.mock("@/src/api/auth", () => ({
  parentSignup: (displayName: string, value: typeof mockProof) =>
    mockParentSignup(displayName, value),
}));

jest.mock("@/src/api/consent", () => ({
  recordConsent: (email: string, value: string) => mockRecordConsent(email, value),
}));

jest.mock("@/src/auth/consentProof", () => ({
  ...jest.requireActual("@/src/auth/consentProof"),
  generateParentConsentNonce: () => mockNonce,
  storePendingParentConsentProof: (value: typeof mockProof) => mockStoreProof(value),
  readPendingParentConsentProof: () => mockStoredProof,
  clearPendingParentConsentProof: (value: typeof mockProof) => mockClearProof(value),
}));

function successfulReceipt() {
  return {
    id: mockProof.receiptId,
    recorded_at: "2026-07-11T12:00:00Z",
    policy_version: mockProof.policyVersion,
  };
}

async function fillAndSubmit(tree: renderer.ReactTestRenderer): Promise<void> {
  act(() => {
    tree.root.findByType(TextInput).props.onChangeText("parent@example.com");
    tree.root.findByProps({ testID: "consent-agreement-checkbox" }).props.onPress();
  });
  await act(async () => {
    tree.root.findByProps({ testID: "consent-submit-button" }).props.onPress();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("ConsentScreen", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockStoredProof = null;
    useAuthSession.getState().setAnonymous();
  });

  it("keeps the email control readable and labelled on the light parent surface", () => {
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<ConsentScreen />);
    });

    const input = tree.root.findByType(TextInput);
    expect(input.props.accessibilityLabel).toBe("Parent or guardian email");
    expect(input.props.autoComplete).toBe("email");
    expect(input.props.placeholderTextColor).toBe("#6b7280");
    expect(StyleSheet.flatten(input.props.style)).toMatchObject({
      color: "#1f2937",
      backgroundColor: "#fff",
    });
    expect(
      tree.root.findByProps({ testID: "consent-agreement-checkbox" }).props,
    ).toMatchObject({
      accessibilityRole: "checkbox",
      accessibilityState: { checked: false },
    });

    act(() => tree.unmount());
  });

  it("reuses one private nonce after an ambiguous recording error", async () => {
    mockRecordConsent
      .mockRejectedValueOnce(new Error("temporary outage"))
      .mockResolvedValueOnce(successfulReceipt());

    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<ConsentScreen />);
    });
    await fillAndSubmit(tree);

    expect(tree.root.findByProps({ testID: "consent-error" })).toBeTruthy();
    expect(
      tree.root.findByProps({ testID: "consent-submit-button" }).props.disabled,
    ).toBe(false);

    await act(async () => {
      tree.root.findByProps({ testID: "consent-submit-button" }).props.onPress();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockRecordConsent).toHaveBeenNthCalledWith(1, "parent@example.com", mockNonce);
    expect(mockRecordConsent).toHaveBeenNthCalledWith(2, "parent@example.com", mockNonce);
    expect(mockStoreProof).toHaveBeenCalledWith(mockProof);
    expect(mockClearProof).not.toHaveBeenCalled();
    const rendered = JSON.stringify(tree.toJSON());
    expect(rendered).toContain("Consent recorded");
    expect(rendered).toContain(mockProof.receiptId);
    expect(rendered).not.toContain(mockNonce);

    act(() => tree.unmount());
  });

  it("links an existing parent without recording a duplicate receipt", async () => {
    useAuthSession.getState().setAuthenticated({
      id: "parent-1",
      entra_oid: "entra-1",
      role: "parent",
      display_name: "Alex Adult",
    });
    mockRecordConsent.mockResolvedValue(successfulReceipt());
    mockParentSignup
      .mockRejectedValueOnce(new Error("temporary linkage outage"))
      .mockResolvedValueOnce({ id: "parent-1" });

    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<ConsentScreen />);
    });
    await fillAndSubmit(tree);

    expect(mockRecordConsent).toHaveBeenCalledTimes(1);
    expect(mockParentSignup).toHaveBeenCalledWith("Alex Adult", mockProof);
    expect(mockClearProof).not.toHaveBeenCalled();
    expect(tree.root.findByProps({ testID: "consent-link-error" })).toBeTruthy();

    await act(async () => {
      tree.root.findByProps({ testID: "consent-next-button" }).props.onPress();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockRecordConsent).toHaveBeenCalledTimes(1);
    expect(mockParentSignup).toHaveBeenCalledTimes(2);
    expect(mockParentSignup).toHaveBeenLastCalledWith("Alex Adult", mockProof);
    expect(mockClearProof).toHaveBeenCalledWith(mockProof);
    expect(JSON.stringify(tree.toJSON())).not.toContain(mockNonce);

    act(() => tree.unmount());
  });
});
