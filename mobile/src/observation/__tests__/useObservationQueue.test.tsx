import { useEffect } from "react";
import renderer, { act } from "react-test-renderer";

import type { QueuedObservation } from "@/src/observation/queueTypes";
import { useObservationQueue } from "@/src/observation/useObservationQueue";
import {
  listQueuedObservations,
  subscribeObservationQueue,
} from "@/src/observation/observationQueue";

jest.mock("@/src/observation/observationQueue", () => ({
  listQueuedObservations: jest.fn(),
  subscribeObservationQueue: jest.fn(() => () => undefined),
}));

const listQueue = listQueuedObservations as jest.MockedFunction<
  typeof listQueuedObservations
>;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function item(ownerUserId: string): QueuedObservation {
  return {
    ownerUserId,
    submissionKey: `submission-${ownerUserId}`,
  } as QueuedObservation;
}

function Harness({
  ownerUserId,
  onItems,
}: {
  ownerUserId: string | null;
  onItems: (owners: string[]) => void;
}) {
  const queue = useObservationQueue(ownerUserId);
  useEffect(() => {
    onItems(queue.items.map((record) => record.ownerUserId));
  }, [onItems, queue.items]);
  return null;
}

describe("owner-scoped Observation queue hook", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (subscribeObservationQueue as jest.Mock).mockReturnValue(() => undefined);
  });

  it("suppresses an old owner's SQLite result after an account switch", async () => {
    const kidOne = deferred<QueuedObservation[]>();
    const kidTwo = deferred<QueuedObservation[]>();
    listQueue.mockImplementation((ownerUserId) =>
      ownerUserId === "kid-1" ? kidOne.promise : kidTwo.promise,
    );
    const snapshots: string[][] = [];
    const onItems = (owners: string[]) => snapshots.push(owners);
    let tree!: renderer.ReactTestRenderer;

    await act(async () => {
      tree = renderer.create(
        <Harness ownerUserId="kid-1" onItems={onItems} />,
      );
      await Promise.resolve();
    });
    await act(async () => {
      tree.update(<Harness ownerUserId="kid-2" onItems={onItems} />);
      await Promise.resolve();
    });
    await act(async () => {
      kidTwo.resolve([item("kid-2")]);
      await Promise.resolve();
    });
    expect(snapshots.at(-1)).toEqual(["kid-2"]);

    await act(async () => {
      kidOne.resolve([item("kid-1")]);
      await Promise.resolve();
    });
    expect(snapshots.at(-1)).toEqual(["kid-2"]);

    act(() => tree.unmount());
  });
});
