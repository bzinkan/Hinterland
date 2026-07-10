export type ImperativeRequestTicket = {
  generation: number;
  signal: AbortSignal;
};

let generation = 0;
let controller = new AbortController();

export class ImperativeRequestSupersededError extends Error {
  constructor() {
    super("The active account changed while the request was running.");
    this.name = "ImperativeRequestSupersededError";
  }
}

/** Abort every imperative request issued under the previous bearer token. */
export function rotateImperativeRequestBoundary(): void {
  controller.abort();
  generation += 1;
  controller = new AbortController();
}

export function beginImperativeRequest(): ImperativeRequestTicket {
  return { generation, signal: controller.signal };
}

export function imperativeRequestIsCurrent(
  ticket: ImperativeRequestTicket,
): boolean {
  return ticket.generation === generation && !ticket.signal.aborted;
}

export async function runImperativeRequest<T>(
  operation: (signal: AbortSignal) => Promise<T>,
): Promise<T> {
  const ticket = beginImperativeRequest();
  let result: T;
  try {
    result = await operation(ticket.signal);
  } catch (error) {
    if (!imperativeRequestIsCurrent(ticket)) {
      throw new ImperativeRequestSupersededError();
    }
    throw error;
  }
  if (!imperativeRequestIsCurrent(ticket)) {
    throw new ImperativeRequestSupersededError();
  }
  return result;
}
