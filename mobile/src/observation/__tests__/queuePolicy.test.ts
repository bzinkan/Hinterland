import {
  OBSERVATION_QUEUE_CAP,
  ObservationQueueFullError,
  ObservationRetryNotAllowedError,
  admitDeviceQueueEntry,
  assertDeviceQueueCapacity,
  exactRetryStage,
  mayRenderLocalPhoto,
  persistFailureStage,
  withSubmissionQueueLock,
} from "@/src/observation/queuePolicy";

describe("Observation queue repository policy", () => {
  it("enforces the 50-entry cap across every device owner", async () => {
    const getFirstAsync = jest
      .fn()
      .mockResolvedValueOnce({ count: OBSERVATION_QUEUE_CAP - 1 })
      .mockResolvedValueOnce({ count: OBSERVATION_QUEUE_CAP });
    const database = { getFirstAsync };

    await expect(assertDeviceQueueCapacity(database)).resolves.toBeUndefined();
    await expect(assertDeviceQueueCapacity(database)).rejects.toBeInstanceOf(
      ObservationQueueFullError,
    );

    const sql = getFirstAsync.mock.calls[0][0] as string;
    expect(sql).toContain("FROM observation_queue");
    expect(sql).not.toContain("owner_user_id");
  });

  it.each(["ready", "presigned", "uploaded"] as const)(
    "retains the exact %s stage after a retryable failure",
    (stage) => {
      expect(persistFailureStage(stage, true)).toEqual({
        persistedStage: stage,
        failureStage: stage,
        lastFailureRetryable: true,
      });
      expect(exactRetryStage(stage, true)).toBe(stage);
    },
  );

  it.each(["ready", "presigned", "uploaded"] as const)(
    "records %s as the failure origin without allowing blind retry",
    (stage) => {
      expect(persistFailureStage(stage, false)).toEqual({
        persistedStage: "needs_attention",
        failureStage: stage,
        lastFailureRetryable: false,
      });
      expect(() => exactRetryStage(stage, false)).toThrow(
        ObservationRetryNotAllowedError,
      );
    },
  );

  it("renders a local child photo only before the payload is frozen", () => {
    expect(
      mayRenderLocalPhoto({
        payloadFrozen: false,
        stage: "ready",
        localUri: "file:///private.jpg",
      }),
    ).toBe(true);

    for (const state of [
      { payloadFrozen: true, stage: "ready" as const, localUri: "file:///private.jpg" },
      { payloadFrozen: true, stage: "uploaded" as const, localUri: "file:///private.jpg" },
      { payloadFrozen: true, stage: "complete" as const, localUri: "" },
    ]) {
      expect(mayRenderLocalPhoto(state)).toBe(false);
    }
  });

  it("admits only one of two concurrent inserts when the device starts at 49", async () => {
    class ExclusiveQueueDatabase {
      count = OBSERVATION_QUEUE_CAP - 1;
      private tail: Promise<void> = Promise.resolve();

      getFirstAsync<T>(): Promise<T> {
        return Promise.resolve({ count: this.count } as T);
      }

      withExclusiveTransactionAsync(
        task: (transaction: ExclusiveQueueDatabase) => Promise<void>,
      ): Promise<void> {
        const run = this.tail.then(() => task(this));
        this.tail = run.catch(() => undefined);
        return run;
      }
    }

    const database = new ExclusiveQueueDatabase();
    const admit = () =>
      admitDeviceQueueEntry(
        database,
        async () => false,
        async () => {
          await Promise.resolve();
          database.count += 1;
        },
      );

    const results = await Promise.allSettled([admit(), admit()]);

    expect(results.filter((result) => result.status === "fulfilled")).toHaveLength(1);
    expect(results.filter((result) => result.status === "rejected")).toHaveLength(1);
    expect(database.count).toBe(OBSERVATION_QUEUE_CAP);
    expect(
      results.find((result) => result.status === "rejected"),
    ).toMatchObject({ reason: expect.any(ObservationQueueFullError) });
  });

  it("serializes duplicate picker callbacks so the replay never touches the winner's file", async () => {
    let rowExists = false;
    let copies = 0;
    let winnerDeletes = 0;
    let releaseFirst!: () => void;
    const firstCanCommit = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    const persistSameKey = () =>
      withSubmissionQueueLock("01J00000000000000000000000", async () => {
        if (rowExists) return "existing" as const;
        copies += 1;
        await firstCanCommit;
        rowExists = true;
        return "inserted" as const;
      }).catch((error) => {
        if (rowExists) winnerDeletes += 1;
        throw error;
      });

    const first = persistSameKey();
    await Promise.resolve();
    const second = persistSameKey();
    releaseFirst();

    await expect(first).resolves.toBe("inserted");
    await expect(second).resolves.toBe("existing");
    expect(copies).toBe(1);
    expect(winnerDeletes).toBe(0);
  });
});
