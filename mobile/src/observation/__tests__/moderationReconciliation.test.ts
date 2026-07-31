import type {
  Observation,
  ObservationListItem,
  ObservationListResponse,
} from "@/src/api/observations";
import {
  OBSERVATION_RECONCILE_INTERVAL_MS,
  W1_NOOP_SERVER_TRANSITION_SLO_MS,
  W1_NOOP_VISIBLE_TRANSITION_SLO_MS,
  detailPresentationPollInterval,
  listPresentationPollInterval,
} from "@/src/observation/moderationReconciliation";

function item(
  child_presentation_status: ObservationListItem["child_presentation_status"],
): ObservationListItem {
  return {
    id: "observation",
    photo_id: "photo",
    geohash4: null,
    observed_at: "2026-07-31T00:00:00Z",
    location_source: "none",
    taxon_id: null,
    species_name: null,
    identification_source: "unknown",
    place_name: null,
    child_presentation_status,
    dispatch_status: "complete",
  };
}

function pages(
  ...statuses: ObservationListItem["child_presentation_status"][]
): { pages: ObservationListResponse[] } {
  return {
    pages: [
      {
        items: statuses.map(item),
        next_cursor: null,
      },
    ],
  };
}

describe("Observation moderation reconciliation", () => {
  it("polls the Journal only while the server response remains nonterminal", () => {
    expect(listPresentationPollInterval(pages("pending"))).toBe(
      OBSERVATION_RECONCILE_INTERVAL_MS,
    );
    expect(listPresentationPollInterval(pages("processing"))).toBe(
      OBSERVATION_RECONCILE_INTERVAL_MS,
    );
    expect(
      listPresentationPollInterval(pages("pilot_private", "failed")),
    ).toBe(false);
    expect(listPresentationPollInterval(undefined)).toBe(false);
  });

  it("polls an open detail independently from the list cache", () => {
    const pending = {
      child_presentation_status: "pending",
    } as Pick<Observation, "child_presentation_status">;
    const pilotPrivate = {
      child_presentation_status: "pilot_private",
    } as Pick<Observation, "child_presentation_status">;

    expect(detailPresentationPollInterval(pending)).toBe(
      OBSERVATION_RECONCILE_INTERVAL_MS,
    );
    expect(detailPresentationPollInterval(pilotPrivate)).toBe(false);
  });

  it("keeps the mobile-visible bound separate from backend transition time", () => {
    expect(W1_NOOP_SERVER_TRANSITION_SLO_MS).toBe(150_000);
    expect(OBSERVATION_RECONCILE_INTERVAL_MS).toBe(5_000);
    expect(W1_NOOP_VISIBLE_TRANSITION_SLO_MS).toBe(
      W1_NOOP_SERVER_TRANSITION_SLO_MS +
        2 * OBSERVATION_RECONCILE_INTERVAL_MS,
    );
  });
});
