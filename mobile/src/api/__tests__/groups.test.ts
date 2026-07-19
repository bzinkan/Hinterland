import {
  archiveGroup,
  createAdultInvitation,
  listAdultInvitations,
  listOwnedChildren,
  placeOwnedChildInGroup,
  redeemAdultInvitation,
  reissueKidHandoff,
  removeAdultMember,
  revokeAdultInvitation,
  updateGroup,
} from "@/src/api/groups";

jest.mock("@/src/auth/token", () => ({
  getBearerTokenSnapshot: jest.fn(async () => ({
    token: "adult-token",
    generation: 0,
  })),
  bearerTokenSnapshotIsCurrent: jest.fn(() => true),
}));

describe("kid handoff reissue contract", () => {
  it("posts to the exact owner-scoped existing-kid route without a body", async () => {
    const responseBody = {
      id: "kid-1",
      display_name: "Sparrow",
      age_band: "9-10",
      handoff_token: "one-time-token",
      expires_at: "2026-07-14T23:15:00Z",
    };
    globalThis.fetch = jest.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => responseBody,
    })) as unknown as typeof fetch;

    await expect(reissueKidHandoff("group-1", "kid-1")).resolves.toEqual(
      responseBody,
    );

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://jest.invalid/v1/groups/group-1/kids/kid-1/handoff",
      expect.objectContaining({
        method: "POST",
        body: undefined,
        headers: expect.objectContaining({
          Accept: "application/json",
          Authorization: "Bearer adult-token",
        }),
      }),
    );
  });
});

describe("privacy-safe shared group API contract", () => {
  beforeEach(() => jest.clearAllMocks());

  function jsonResponse(body: unknown, status = 200) {
    globalThis.fetch = jest.fn(async () => ({
      ok: true,
      status,
      json: async () => body,
    })) as unknown as typeof fetch;
  }

  it("uses the opaque removal reference rather than a membership or user id", async () => {
    jsonResponse(undefined, 204);
    await removeAdultMember("group-1", "opaque-removal-ref");
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://jest.invalid/v1/groups/group-1/adult-members/opaque-removal-ref",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("uses owner-only rename and archive routes", async () => {
    jsonResponse({ id: "group-1", name: "Nature Club" });
    await updateGroup("group-1", "Nature Club");
    expect(globalThis.fetch).toHaveBeenLastCalledWith(
      "http://jest.invalid/v1/groups/group-1",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ name: "Nature Club" }) }),
    );

    jsonResponse(undefined, 204);
    await archiveGroup("group-1");
    expect(globalThis.fetch).toHaveBeenLastCalledWith(
      "http://jest.invalid/v1/groups/group-1/archive",
      expect.objectContaining({ method: "POST", body: undefined }),
    );
  });

  it("creates, lists, and revokes invitation metadata on exact routes", async () => {
    jsonResponse({ id: "invite-1", invite_url: "https://parents.example/group-invite#token=secret" }, 201);
    await createAdultInvitation("group-1");
    expect(globalThis.fetch).toHaveBeenLastCalledWith(
      "http://jest.invalid/v1/groups/group-1/adult-invitations",
      expect.objectContaining({ method: "POST", body: undefined }),
    );

    jsonResponse({ items: [] });
    await listAdultInvitations("group-1");
    expect(globalThis.fetch).toHaveBeenLastCalledWith(
      "http://jest.invalid/v1/groups/group-1/adult-invitations",
      expect.objectContaining({ method: "GET" }),
    );

    jsonResponse(undefined, 204);
    await revokeAdultInvitation("group-1", "invite-1");
    expect(globalThis.fetch).toHaveBeenLastCalledWith(
      "http://jest.invalid/v1/groups/group-1/adult-invitations/invite-1",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("sends the one-time token only in the redemption body", async () => {
    jsonResponse({ group_id: "group-1", joined: true, replayed: false });
    await redeemAdultInvitation("private-token");
    const [url, options] = (globalThis.fetch as jest.Mock).mock.calls[0];
    expect(url).toBe("http://jest.invalid/v1/groups/invitations/redeem");
    expect(url).not.toContain("private-token");
    expect(options).toMatchObject({ method: "POST", body: JSON.stringify({ token: "private-token" }) });
  });

  it("loads only the canonical parent's minimized child inventory", async () => {
    const response = {
      items: [
        {
          id: "child-1",
          display_name: "Finch",
          age_band: "9-10",
          active_group_id: null,
        },
      ],
    };
    jsonResponse(response);
    await expect(listOwnedChildren()).resolves.toEqual(response);
    expect(globalThis.fetch).toHaveBeenLastCalledWith(
      "http://jest.invalid/v1/groups/owned-children",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("places an owned child through the exact group membership route", async () => {
    jsonResponse(undefined, 204);
    await placeOwnedChildInGroup("group-2", "child-1");
    expect(globalThis.fetch).toHaveBeenLastCalledWith(
      "http://jest.invalid/v1/groups/group-2/kids/child-1/membership",
      expect.objectContaining({ method: "POST", body: undefined }),
    );
  });
});
