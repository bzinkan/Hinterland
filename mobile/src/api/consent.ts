import { apiRequest } from "@/src/api/client";
import { ACTIVE_CONSENT_POLICY_VERSION } from "@/src/auth/consentProof";

export {
  ACTIVE_CONSENT_POLICY_VERSION,
  ADULT_QA_CONSENT_POLICY_VERSION,
  W1_CONSENT_POLICY_VERSION,
} from "@/src/auth/consentProof";

export type ConsentResponse = {
  id: string;
  recorded_at: string;
  policy_version: string;
};

export function recordConsent(email: string, nonce: string): Promise<ConsentResponse> {
  return apiRequest<ConsentResponse>("/v1/auth/consent", {
    method: "POST",
    body: {
      email,
      policy_version: ACTIVE_CONSENT_POLICY_VERSION,
      consent_nonce: nonce,
    },
    unauthenticated: true,
  });
}
