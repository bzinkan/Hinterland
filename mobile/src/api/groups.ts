import { apiRequest } from "@/src/api/client";

export type Group = {
  id: string;
  name: string;
  join_code: string;
  owner_user_id: string;
};

export type GroupListResponse = {
  items: Group[];
};

export type RosterMember = {
  user_id: string;
  display_name: string;
  role: string;
  age_band: string | null;
  membership_id: string;
  observation_count: number;
  dex_count: number;
  rarest_tier: string | null;
  last_observed_at: string | null;
};

export type RosterResponse = {
  group: Group;
  items: RosterMember[];
};

export type AgeBand = "9-10" | "11-12" | "13+";

export type CreateKidResponse = {
  id: string;
  display_name: string;
  age_band: string;
  /**
   * One-time, 15-minute backend-signed JWT the kid app exchanges at
   * `POST /v1/auth/kid-exchange` for a long-lived session JWT. Renamed
   * from `custom_token` in Phase 6a when the backend moved to
   * Hinterland-issued RS256 JWTs.
   */
  handoff_token: string;
  expires_at: string;
};

export type KidExchangeResponse = {
  session_token: string;
  expires_at: string;
  user: {
    id: string;
    role: string;
    display_name: string;
  };
};

export function listGroups(): Promise<GroupListResponse> {
  return apiRequest<GroupListResponse>("/v1/groups");
}

export function createGroup(name: string): Promise<Group> {
  return apiRequest<Group>("/v1/groups", { method: "POST", body: { name } });
}

export function listGroupMembers(groupId: string): Promise<RosterResponse> {
  return apiRequest<RosterResponse>(`/v1/groups/${groupId}/members`);
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
