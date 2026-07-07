/**
 * Pure display rules for the Field Journal. Kept out of the component so
 * jest can pin the status -> presentation mapping without a renderer.
 *
 * `photo_status` comes from the joined Photo row on GET /v1/observations/me
 * and is a 1:1 proxy for moderation outcome (worker and review-queue flip
 * photo + observation together): pending = not yet moderated, clean =
 * approved, quarantine = awaiting adult review, deleted = rejected.
 */

import type { DexListItem } from "@/src/api/dex";

export const DEFAULT_JOURNAL_MODE = "photos" as const;

export type JournalMode = "photos" | "species";
export type PhotoDisplayMode = "image" | "reviewing" | "removed";

export function photoDisplayMode(photoStatus: string): PhotoDisplayMode {
  switch (photoStatus) {
    case "clean":
    case "pending":
      // The kid's own Field Journal may show their photo before moderation
      // lands -- it is only ever shown to its owner here. The list
      // endpoint is /me-scoped, and group-visible surfaces (leaderboards
      // etc.) don't render photos.
      return "image";
    case "quarantine":
      return "reviewing";
    case "deleted":
      return "removed";
    default:
      // Unknown/future status: never crash the Field Journal; treat it like a
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
export function journalCaption(speciesName: string | null): string {
  const trimmed = speciesName?.trim();
  return trimmed ? trimmed : "Mystery find";
}

export function speciesDisplayName(item: DexListItem): string {
  return (
    item.common_name?.trim() ||
    item.species_name?.trim() ||
    item.scientific_name?.trim() ||
    `Taxon ${item.taxon_id}`
  );
}

export function speciesSubtitle(item: DexListItem): string {
  const parts = [
    item.scientific_name?.trim(),
    item.iconic_taxon?.trim(),
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(" - ") : "Verified species";
}

export function findCountLabel(count: number): string {
  return `${count} ${count === 1 ? "find" : "finds"}`;
}

/**
 * True while a signed URL is safe to hand to <Image>. 30s margin: a URL
 * used right at the SAS edge 403s mid-download. Callers fall back to the
 * loading placeholder when false -- the background refetch re-mints.
 */
export function isUrlUsable(expiresAt: string): boolean {
  return Date.parse(expiresAt) - 30_000 > Date.now();
}
