import { apiRequest } from "@/src/api/client";

export type ExpeditionRelevance = {
  level: "great_here" | "tricky_here" | "unknown";
  reason: string | null;
};

export type ExpeditionSummary = {
  id: string;
  title: string;
  subtitle: string | null;
  tier: number;
  duration_minutes: number;
  environments: string[];
  theme: ExpeditionTheme;
  learning_goal: string | null;
  difficulty_label: string | null;
  preview_enabled: boolean;
  unlock_hint: string | null;
  intro: string;
  // Optional -- older backends omit it; the card renders nothing then.
  relevance?: ExpeditionRelevance;
};

export type ExpeditionTheme =
  | "warmup"
  | "food_web"
  | "pollinators"
  | "decomposers"
  | "trees"
  | "wetland"
  | "invasive"
  | "urban"
  | "seasonal";

export type AvailableListResponse = {
  items: ExpeditionSummary[];
  locked_preview_items?: ExpeditionSummary[];
};

export function listAvailableExpeditions(
  geohash4?: string | null,
  signal?: AbortSignal,
): Promise<AvailableListResponse> {
  const params = new URLSearchParams();
  if (geohash4) params.set("geohash4", geohash4);
  const query = params.toString();
  return apiRequest<AvailableListResponse>(
    `/v1/expeditions/available${query ? `?${query}` : ""}`,
    { signal },
  );
}

export type StartResponse = {
  expedition_id: string;
  started_at: string;
};

export function startExpedition(expeditionId: string): Promise<StartResponse> {
  return apiRequest<StartResponse>(`/v1/expeditions/${expeditionId}/start`, {
    method: "POST",
  });
}

export function restartExpedition(
  expeditionId: string,
): Promise<StartResponse> {
  return apiRequest<StartResponse>(`/v1/expeditions/${expeditionId}/restart`, {
    method: "POST",
  });
}

export type FocusResponse = {
  expedition_id: string;
  focused_at: string;
};

export function focusExpedition(expeditionId: string): Promise<FocusResponse> {
  return apiRequest<FocusResponse>(`/v1/expeditions/${expeditionId}/focus`, {
    method: "POST",
  });
}

export type StepProgress = {
  id: string;
  description: string;
  hint: string | null;
  tag_prompt: StepTagPrompt | null;
  completed_at: string | null;
};

export type StepTagPrompt = {
  key: "life_stage";
  question: string;
  options: { value: string; label: string }[];
};

export type ProgressItem = {
  expedition_id: string;
  title: string;
  subtitle: string | null;
  intro: string;
  outro: string;
  theme: ExpeditionTheme;
  learning_goal: string | null;
  difficulty_label: string | null;
  started_at: string;
  completed_at: string | null;
  focused_at: string | null;
  completed_step_count: number;
  total_step_count: number;
  // Steps in content order (server sorts; the client never reorders).
  steps: StepProgress[];
};

export type MyProgressResponse = {
  active_expedition_id: string | null;
  items: ProgressItem[];
};

export function listMyExpeditions(signal?: AbortSignal): Promise<MyProgressResponse> {
  return apiRequest<MyProgressResponse>("/v1/expeditions/me", { signal });
}
