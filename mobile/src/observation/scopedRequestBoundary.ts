export class ScopedRequestSupersededError extends Error {
  constructor() {
    super("The observation screen changed while the request was running.");
    this.name = "ScopedRequestSupersededError";
  }
}

/** Abort and generation-check imperative work owned by one rendered scope. */
export class ScopedRequestBoundary {
  private generation = 0;
  private controller = new AbortController();

  invalidate(): void {
    this.controller.abort();
    this.generation += 1;
    this.controller = new AbortController();
  }

  async run<T>(operation: (signal: AbortSignal) => Promise<T>): Promise<T> {
    const generation = this.generation;
    const signal = this.controller.signal;
    try {
      const result = await operation(signal);
      if (!this.isCurrent(generation, signal)) {
        throw new ScopedRequestSupersededError();
      }
      return result;
    } catch (error) {
      if (!this.isCurrent(generation, signal)) {
        throw new ScopedRequestSupersededError();
      }
      throw error;
    }
  }

  private isCurrent(generation: number, signal: AbortSignal): boolean {
    return generation === this.generation && !signal.aborted;
  }
}
