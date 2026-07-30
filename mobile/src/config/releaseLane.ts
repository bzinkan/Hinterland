import { env, type ReleaseLane } from "@/src/config/env";

export const RELEASE_LANE_HEADER = "X-Hinterland-Release-Lane";

const RELEASE_LANES = new Set<ReleaseLane>([
  "local",
  "w1_internal",
  "adult_qa",
  "production",
]);

export type ReleaseLaneProbe = {
  expected: ReleaseLane;
  reported: ReleaseLane;
  apiVersion: string;
};

export class ReleaseLaneConfigurationError extends Error {
  constructor(
    message: string,
    public readonly expected: ReleaseLane,
    public readonly reported: string | null,
  ) {
    super(message);
    this.name = "ReleaseLaneConfigurationError";
  }
}

export function validateReleaseLaneHeader(response: Response): ReleaseLane | null {
  if (!env.enforceReleaseLane) return null;
  return requireReleaseLaneHeader(response, env.expectedApiLane);
}

export function requireReleaseLaneHeader(
  response: Response,
  expected: ReleaseLane,
): ReleaseLane {
  const reported = response.headers.get(RELEASE_LANE_HEADER);
  if (reported !== expected) {
    throw new ReleaseLaneConfigurationError(
      "This app is connected to the wrong Hinterland release lane.",
      expected,
      reported,
    );
  }
  return expected;
}

export async function probeReleaseLane(
  signal?: AbortSignal,
): Promise<ReleaseLaneProbe> {
  if (!env.enforceReleaseLane) {
    return {
      expected: env.expectedApiLane,
      reported: env.expectedApiLane,
      apiVersion: "not enforced",
    };
  }

  let response: Response;
  try {
    response = await fetch(`${env.apiBaseUrl}/v1/meta`, {
      method: "GET",
      headers: { Accept: "application/json" },
      signal,
    });
  } catch (error) {
    if (error instanceof ReleaseLaneConfigurationError) throw error;
    throw new ReleaseLaneConfigurationError(
      "The Adult QA server could not be verified. Capture and submission remain locked.",
      env.expectedApiLane,
      null,
    );
  }

  return verifyReleaseLaneMeta(response, env.expectedApiLane);
}

export async function verifyReleaseLaneMeta(
  response: Response,
  expected: ReleaseLane,
): Promise<ReleaseLaneProbe> {
  const headerLane = requireReleaseLaneHeader(response, expected);
  if (!response.ok) {
    throw new ReleaseLaneConfigurationError(
      `The Adult QA server verification returned HTTP ${response.status}.`,
      expected,
      headerLane,
    );
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new ReleaseLaneConfigurationError(
      "The Adult QA server returned an invalid verification response.",
      expected,
      headerLane,
    );
  }

  if (!isMetaBody(body) || body.release_lane !== expected) {
    const reported =
      isObject(body) && typeof body.release_lane === "string"
        ? body.release_lane
        : null;
    throw new ReleaseLaneConfigurationError(
      "The Adult QA server identity did not match this app.",
      expected,
      reported,
    );
  }
  if (
    expected === "adult_qa" &&
    body.capabilities?.integrations?.public_inat_submission_enabled !== false
  ) {
    throw new ReleaseLaneConfigurationError(
      "Public iNaturalist contribution is not safely disabled for this Adult QA server.",
      expected,
      body.release_lane,
    );
  }

  return {
    expected,
    reported: body.release_lane,
    apiVersion: body.version,
  };
}

function isMetaBody(value: unknown): value is {
  release_lane: ReleaseLane;
  version: string;
  capabilities?: {
    integrations?: {
      public_inat_submission_enabled?: boolean;
    };
  };
} {
  return (
    isObject(value) &&
    typeof value.release_lane === "string" &&
    RELEASE_LANES.has(value.release_lane as ReleaseLane) &&
    typeof value.version === "string" &&
    value.version.length > 0
  );
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
