import {
  ImperativeRequestSupersededError,
  beginImperativeRequest,
  imperativeRequestIsCurrent,
  rotateImperativeRequestBoundary,
  runImperativeRequest,
} from "@/src/auth/requestBoundary";

describe("imperative auth request boundary", () => {
  it("aborts and invalidates prior-token tickets", () => {
    const ticket = beginImperativeRequest();
    rotateImperativeRequestBoundary();

    expect(ticket.signal.aborted).toBe(true);
    expect(imperativeRequestIsCurrent(ticket)).toBe(false);
  });

  it("rejects a response that resolves after token rotation", async () => {
    let resolve: ((value: string) => void) | undefined;
    const promise = runImperativeRequest(
      () => new Promise<string>((done) => { resolve = done; }),
    );
    rotateImperativeRequestBoundary();
    resolve?.("stale");

    await expect(promise).rejects.toBeInstanceOf(
      ImperativeRequestSupersededError,
    );
  });

  it("normalizes an aborted prior-token request as superseded", async () => {
    const promise = runImperativeRequest(
      (signal) =>
        new Promise<string>((_resolve, reject) => {
          signal.addEventListener("abort", () => {
            const error = new Error("aborted");
            error.name = "AbortError";
            reject(error);
          });
        }),
    );

    rotateImperativeRequestBoundary();

    await expect(promise).rejects.toBeInstanceOf(
      ImperativeRequestSupersededError,
    );
  });
});
