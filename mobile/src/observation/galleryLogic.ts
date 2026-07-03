/**
 * Pure display rules for the Home gallery. Kept out of the component so
 * jest can pin the status -> presentation mapping without a renderer.
 *
 * `photo_status` comes from the joined Photo row on GET /v1/observations/me
 * and is a 1:1 proxy for moderation outcome (worker and review-queue flip
 * photo + observation together): pending = not yet moderated, clean =
 * approved, quarantine = awaiting adult review, deleted = rejected.
 */

export type PhotoDisplayMode = "image" | "reviewing" | "removed";

export function photoDisplayMode(photoStatus: string): PhotoDisplayMode {
  switch (photoStatus) {
    case "clean":
    case "pending":
      // The kid's own gallery may show their photo before moderation
      // lands -- it is only ever shown to its owner here. The list
      // endpoint is /me-scoped, and group-visible surfaces (leaderboards
      // etc.) don't render photos.
      return "image";
    case "quarantine":
      return "reviewing";
    case "deleted":
      return "removed";
    default:
      // Unknown/future status: never crash the gallery; treat it like a
      // photo we can't show yet.
      return "reviewing";
  }
}

/** True while moderation hasn't looked at the photo yet -- the card shows
 * the image with a small "checking" badge so the state is honest without
 * being scary. */
export function isAwaitingModeration(photoStatus: string): boolean {
  return photoStatus === "pending";
}

/** Card caption. Kids often skip the species pick; "Mystery find" reads
 * better than a null. */
export function galleryCaption(speciesName: string | null): string {
  return speciesName ?? "Mystery find";
}
