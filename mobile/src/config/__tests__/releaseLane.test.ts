import {
  fetchReleaseLaneProbe,
  ReleaseLaneConfigurationError,
  requireReleaseLaneHeader,
  verifyReleaseLaneMeta,
} from "@/src/config/releaseLane";

function response(
  body: unknown,
  lane: string | null,
  status = 200,
): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get: jest.fn((name: string) =>
        name.toLowerCase() === "x-hinterland-release-lane" ? lane : null,
      ),
    },
    json: jest.fn(async () => body),
  } as unknown as Response;
}

describe("Adult QA release-lane verification", () => {
  test("accepts only matching header and metadata body", async () => {
    await expect(
      verifyReleaseLaneMeta(
        response(
          {
            release_lane: "adult_qa",
            version: "abc1234",
            name: "Hinterland",
            capabilities: {
              integrations: { public_inat_submission_enabled: false },
            },
          },
          "adult_qa",
        ),
        "adult_qa",
      ),
    ).resolves.toEqual({
      expected: "adult_qa",
      reported: "adult_qa",
      apiVersion: "abc1234",
    });
  });

  test.each([null, "w1_internal", "production"])(
    "fails closed for response header %p",
    (lane) => {
      expect(() =>
        requireReleaseLaneHeader(
          response({ release_lane: "adult_qa", version: "abc1234" }, lane),
          "adult_qa",
        ),
      ).toThrow(ReleaseLaneConfigurationError);
    },
  );

  test("rejects a body/header disagreement", async () => {
    await expect(
      verifyReleaseLaneMeta(
        response(
          { release_lane: "w1_internal", version: "abc1234" },
          "adult_qa",
        ),
        "adult_qa",
      ),
    ).rejects.toMatchObject({
      name: "ReleaseLaneConfigurationError",
      expected: "adult_qa",
      reported: "w1_internal",
    });
  });

  test("rejects malformed metadata despite a matching header", async () => {
    await expect(
      verifyReleaseLaneMeta(response([], "adult_qa"), "adult_qa"),
    ).rejects.toBeInstanceOf(ReleaseLaneConfigurationError);
  });

  test("rejects non-success verification responses", async () => {
    await expect(
      verifyReleaseLaneMeta(
        response(
          { release_lane: "adult_qa", version: "abc1234" },
          "adult_qa",
          503,
        ),
        "adult_qa",
      ),
    ).rejects.toThrow("HTTP 503");
  });

  test("blocks Adult QA when public iNaturalist submission is enabled or unreported", async () => {
    await expect(
      verifyReleaseLaneMeta(
        response(
          {
            release_lane: "adult_qa",
            version: "abc1234",
            capabilities: {
              integrations: { public_inat_submission_enabled: true },
            },
          },
          "adult_qa",
        ),
        "adult_qa",
      ),
    ).rejects.toThrow("Public iNaturalist contribution");

    await expect(
      verifyReleaseLaneMeta(
        response(
          { release_lane: "adult_qa", version: "abc1234" },
          "adult_qa",
        ),
        "adult_qa",
      ),
    ).rejects.toThrow("Public iNaturalist contribution");
  });

  test("fails closed instead of spinning forever when the QA API does not respond", async () => {
    jest.useFakeTimers();
    const originalFetch = global.fetch;
    global.fetch = jest.fn(
      (_input: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener(
            "abort",
            () => reject(new Error("aborted")),
            { once: true },
          );
        }),
    ) as typeof fetch;

    try {
      const result = fetchReleaseLaneProbe(
        "https://qa-api.example.invalid",
        "adult_qa",
        undefined,
        25,
      );
      const expectation = expect(result).rejects.toMatchObject({
        name: "ReleaseLaneConfigurationError",
        expected: "adult_qa",
        reported: null,
        message: expect.stringContaining("timed out"),
      });
      await jest.advanceTimersByTimeAsync(25);
      await expectation;
    } finally {
      global.fetch = originalFetch;
      jest.useRealTimers();
    }
  });
});
