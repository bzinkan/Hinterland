/** Stable React scope for all identification presentation state. */
export function identificationScopeKey(
  ownerUserId: string,
  observationId: string,
): string {
  return JSON.stringify([ownerUserId, observationId]);
}

export function emptyIdentificationPresentation(expectedRevision: number) {
  return {
    catalogQuery: "",
    catalogResults: [],
    suggestions: [],
    manualSpecies: "",
    revision: expectedRevision,
    busy: null,
    searching: false,
    message: null,
  } as const;
}

export function identificationResponseMatchesScope(
  response: { id: string; user_id: string },
  active: { ownerUserId: string | null; observationId: string | null },
): boolean {
  return (
    active.ownerUserId !== null &&
    active.observationId !== null &&
    response.user_id === active.ownerUserId &&
    response.id === active.observationId
  );
}
