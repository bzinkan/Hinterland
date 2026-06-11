/**
 * Placeholder zone colors until the authored island + zone material
 * palettes land (asset milestones A3/A8). Mirrors the hue families of the
 * 2D ZONE_TOKENS and the pipeline palette (scripts/sanctuary_assets/palette).
 */

import type { SanctuaryZoneId } from "@/src/api/sanctuary";

export const ZONE_PLACEHOLDER_COLOR: Record<SanctuaryZoneId, string> = {
  meadow: "#8FBC6F",
  woodland: "#528C40",
  pond: "#7FB8C4",
  urban: "#C9CDD1",
  soil: "#6E5235",
  sky: "#F2F5F7",
  elsewhere: "#B5A8C9",
};

/** Element accent per zone (slightly deeper than the ground tint). */
export const ZONE_ACCENT_COLOR: Record<SanctuaryZoneId, string> = {
  meadow: "#5C8A2A",
  woodland: "#3F6B40",
  pond: "#3F7B86",
  urban: "#4F4F4F",
  soil: "#4A3826",
  sky: "#4A6B8A",
  elsewhere: "#6B6573",
};

/** Dormant warm grey: zones asleep, mystery silhouettes. Asleep, not locked. */
export const DORMANT_COLOR = "#84867F";
export const SILHOUETTE_COLOR = "#5E605C";
