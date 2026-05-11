import { apiRequest } from "@/src/api/client";

export type UserResponse = {
  id: string;
  firebase_uid: string;
  role: string;
  display_name: string;
};

export type CurrentUser = {
  id: string;
  firebase_uid: string;
  role: string;
  display_name: string;
};

export function parentSignup(displayName: string): Promise<UserResponse> {
  return apiRequest<UserResponse>("/v1/auth/parent-signup", {
    method: "POST",
    body: { display_name: displayName },
  });
}

export function getMe(): Promise<CurrentUser> {
  return apiRequest<CurrentUser>("/v1/me");
}
