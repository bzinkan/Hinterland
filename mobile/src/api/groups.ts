import { apiRequest } from "@/src/api/client";

export type GroupPermissions = {
  can_rename: boolean;
  can_archive: boolean;
  can_invite_parents: boolean;
  can_manage_invitations: boolean;
  can_remove_adults: boolean;
  can_add_child: boolean;
};

export type Group = {
  id: string;
  name: string;
  is_owner: boolean;
  adult_count: number;
  child_count: number;
  own_children_count: number;
  permissions: GroupPermissions;
};

export type GroupListResponse = {
  items: Group[];
};

export type AdultRosterMember = {
  removal_ref: string | null;
  display_name: string;
  is_owner: boolean;
  status: string;
};

export type OwnChildRosterMember = {
  user_id: string;
  display_name: string;
  age_band: string | null;
  status: string;
  observation_count: number;
  dex_count: number;
  rarest_tier: string | null;
  last_observed_at: string | null;
};

export type RosterResponse = {
  group: Group;
  adults: AdultRosterMember[];
  own_children: OwnChildRosterMember[];
  other_child_count: number;
};

export type OwnedChildInventoryItem = {
  id: string;
  display_name: string;
  age_band: string | null;
  active_group_id: string | null;
};

export type OwnedChildInventoryResponse = {
  items: OwnedChildInventoryItem[];
};

export type AgeBand = "9-10" | "11-12" | "13+";

export type CreateKidResponse = {
  id: string;
  display_name: string;
  age_band: string;
  /** One-time response value. Never store this in a query cache or log it. */
  handoff_token: string;
  expires_at: string;
};

export type AdultInvitationState = "pending" | "redeemed" | "revoked" | "expired";

export type AdultInvitation = {
  id: string;
  state: AdultInvitationState;
  created_at: string;
  expires_at: string;
  redeemed_at: string | null;
  revoked_at: string | null;
};

export type AdultInvitationCreateResponse = AdultInvitation & {
  /** Contains the one-time token in a URL fragment and is returned only once. */
  invite_url: string;
};

export type AdultInvitationListResponse = {
  items: AdultInvitation[];
};

export type AdultInvitationRedemptionResponse = {
  group_id: string;
  joined: true;
  replayed: boolean;
};

export function listGroups(): Promise<GroupListResponse> {
  return apiRequest<GroupListResponse>("/v1/groups");
}

export function createGroup(name: string): Promise<Group> {
  return apiRequest<Group>("/v1/groups", { method: "POST", body: { name } });
}

export function updateGroup(groupId: string, name: string): Promise<Group> {
  return apiRequest<Group>(`/v1/groups/${groupId}`, {
    method: "PATCH",
    body: { name },
  });
}

export function archiveGroup(groupId: string): Promise<void> {
  return apiRequest<void>(`/v1/groups/${groupId}/archive`, { method: "POST" });
}

export function listGroupMembers(groupId: string): Promise<RosterResponse> {
  return apiRequest<RosterResponse>(`/v1/groups/${groupId}/members`);
}

export function listOwnedChildren(): Promise<OwnedChildInventoryResponse> {
  return apiRequest<OwnedChildInventoryResponse>("/v1/groups/owned-children");
}

export function createKid(
  groupId: string,
  displayName: string,
  ageBand: AgeBand,
): Promise<CreateKidResponse> {
  return apiRequest<CreateKidResponse>(`/v1/groups/${groupId}/kids`, {
    method: "POST",
    body: { display_name: displayName, age_band: ageBand },
  });
}

export function reissueKidHandoff(
  groupId: string,
  kidUserId: string,
): Promise<CreateKidResponse> {
  return apiRequest<CreateKidResponse>(
    `/v1/groups/${groupId}/kids/${kidUserId}/handoff`,
    { method: "POST" },
  );
}

export function placeOwnedChildInGroup(
  groupId: string,
  childId: string,
): Promise<void> {
  return apiRequest<void>(
    `/v1/groups/${groupId}/kids/${childId}/membership`,
    { method: "POST" },
  );
}

export function removeAdultMember(groupId: string, removalRef: string): Promise<void> {
  return apiRequest<void>(
    `/v1/groups/${groupId}/adult-members/${removalRef}`,
    { method: "DELETE" },
  );
}

export function createAdultInvitation(
  groupId: string,
): Promise<AdultInvitationCreateResponse> {
  return apiRequest<AdultInvitationCreateResponse>(
    `/v1/groups/${groupId}/adult-invitations`,
    { method: "POST" },
  );
}

export function listAdultInvitations(
  groupId: string,
): Promise<AdultInvitationListResponse> {
  return apiRequest<AdultInvitationListResponse>(
    `/v1/groups/${groupId}/adult-invitations`,
  );
}

export function revokeAdultInvitation(groupId: string, inviteId: string): Promise<void> {
  return apiRequest<void>(
    `/v1/groups/${groupId}/adult-invitations/${inviteId}`,
    { method: "DELETE" },
  );
}

export function redeemAdultInvitation(token: string): Promise<AdultInvitationRedemptionResponse> {
  return apiRequest<AdultInvitationRedemptionResponse>("/v1/groups/invitations/redeem", {
    method: "POST",
    body: { token },
  });
}
