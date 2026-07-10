import { identificationResponseMatchesScope } from "@/src/observation/identificationPresentation";
import {
  ScopedRequestBoundary,
  ScopedRequestSupersededError,
} from "@/src/observation/scopedRequestBoundary";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("identification route-scope request boundary", () => {
  it("prevents deferred observation A from mutating observation B", async () => {
    const boundary = new ScopedRequestBoundary();
    const response = deferred<{ id: string; user_id: string; name: string }>();
    let active = { ownerUserId: "kid-1", observationId: "observation-a" };
    let renderedName = "Observation B";
    const requestSignals: AbortSignal[] = [];

    const request = boundary
      .run((signal) => {
        requestSignals.push(signal);
        return response.promise;
      })
      .then((observation) => {
        if (identificationResponseMatchesScope(observation, active)) {
          renderedName = observation.name;
        }
      });

    active = { ownerUserId: "kid-1", observationId: "observation-b" };
    boundary.invalidate();
    expect(requestSignals[0]?.aborted).toBe(true);
    response.resolve({
      id: "observation-a",
      user_id: "kid-1",
      name: "Late A identification",
    });

    await expect(request).rejects.toBeInstanceOf(ScopedRequestSupersededError);
    expect(renderedName).toBe("Observation B");
  });

  it("also rejects a response whose owner no longer matches", () => {
    expect(
      identificationResponseMatchesScope(
        { id: "observation-a", user_id: "kid-1" },
        { ownerUserId: "kid-2", observationId: "observation-a" },
      ),
    ).toBe(false);
  });
});
