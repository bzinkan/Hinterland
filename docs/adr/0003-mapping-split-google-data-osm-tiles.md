# ADR 0003: Split mapping stack — Google for data, OSM for rendering

- **Status:** Accepted
- **Date:** 2026-04-22
- **Deciders:** Solo author
- **Supersedes:** —
- **Related:** ADR 0002 (LLMs are author-time, not runtime)

## Context

Hinterland has two separate mapping needs, each with different requirements and different "right answers":

1. **In-app map rendering.** Phase 2's territory claim view. A kid sees tiles they've unlocked by observing in different geohash cells. They pan around, see their group's claims, maybe a teacher zooms in to a field-trip location. This is UI surface area.
2. **Location data enrichment.** Turning `(40.71, -74.00)` into "near Central Park" for observation display, and serving "three closest parks" results in the "Start where you are" onboarding flow. This is backend API calls, mostly cacheable.

Google Maps Platform covers both, but bundling the two choices has real downsides:

- The **Google Maps SDK** for mobile adds an on-screen attribution requirement, exposes UI affordances we don't want in a kids' app (notably a tap-through to Street View from long-press on any location), and charges per-map-view at prices that scale poorly with usage.
- The **Google Maps data APIs** (Geocoding, Places, Directions) are genuinely excellent, priced per-call cheaply enough to be a rounding error at our scale, and — critically — cacheable forever for idempotent lookups.

At the same time, the open mapping ecosystem has matured substantially. MapLibre GL (the community fork of Mapbox GL created after Mapbox's 2020 license change) is production-ready, works on React Native via `@maplibre/maplibre-react-native`, supports offline tile bundling natively, and has no per-view licensing.

## Decision

**Use MapLibre GL with OpenStreetMap tiles for all in-app map rendering. Use Google Maps Platform's data APIs (Geocoding, Places) server-side for location enrichment. Do not embed the Google Maps SDK in the Hinterland mobile app.**

Concretely:

- The mobile app renders the territory map (Phase 2), the optional "where was this observation" map on species detail pages, and the onboarding location preview with `@maplibre/maplibre-react-native` against OSM raster or vector tiles served through a tile provider we control. Initial choice: Stadia Maps or Protomaps (free tiers cover Phase 1 and Phase 2 easily).
- The API Lambda calls Google Geocoding API on observation submission to resolve coordinates to a human-readable place name. Result is cached on the `OBS#` row itself; the geocoding is called once, never again.
- The API Lambda calls Google Places API (Nearby Search) in the "Start where you are" onboarding, filtered by the environment the kid picked. Result is cached for 7 days keyed on `(rounded_lat, rounded_lng, environment)` to stay well under quota and cost.
- Offline tile bundles (Phase 2) ship with the app for the US national-park and urban-park regions where we expect the bulk of early usage. MapLibre supports this natively; Google Maps SDK does not, meaningfully.

Geocoding cache key: the raw lat/lng is rounded to 4 decimal places (~11m precision) before being used as the cache key, so near-duplicate calls from the same neighborhood hit cache. Results live in the species cache partition pattern: `GEO#<rounded_lat>#<rounded_lng>`.

## Consequences

### Positive

- **No per-map-view cost.** The territory map is a core engagement surface. Kids will open it often. Tying that engagement to a per-view billing meter creates exactly the wrong incentive gradient.
- **No Street View surface in a kid app.** Long-pressing a location in the Google Maps SDK offers a tap-through to Street View. Disabling it is possible but fragile across SDK versions, and "one app update and Street View is back on" is a liability we don't need. MapLibre doesn't have this problem because it doesn't have the feature.
- **No forced attribution banner on every map view.** Google Maps SDK requires visible "Google" branding on map views. OSM requires attribution too, but can be placed in an About screen rather than on every map. For a kid app where screen real estate is precious, this matters.
- **Offline tile bundling is achievable.** Kids go on field trips, hike in parks with no signal, and log observations from basements and backyards. MapLibre's offline support means a 10MB bundle of the local region ships with the app; the territory map works with zero connectivity.
- **Predictable costs.** Google data APIs with aggressive caching will sit in the single-digit dollars per month for the foreseeable future. No "usage spike means bill shock" class of incident.
- **Data quality where it matters.** Google's Geocoding and Places are substantially better than OSM's Nominatim for US urban and suburban areas, which is where our users are. We get the best-in-class data layer without the rendering overhead.

### Negative

- **Two vendors instead of one.** Google for data, whoever-provides-OSM-tiles for rendering. Monitoring, outage handling, and billing are split. Mitigation: both are read-heavy and cacheable — a 30-minute tile provider outage is a degraded-mode UX, not a broken app.
- **OSM data quality varies regionally.** In rural US areas and internationally, OSM tiles may be sparser than Google's. Hinterland's initial focus (US schools and families) is fine here; international expansion would warrant a re-look.
- **MapLibre requires styling work.** Google Maps has a default style that just works. MapLibre needs a style JSON to be chosen or authored. Initial plan: use the Stadia "Outdoors" or "Osm Bright" style off the shelf for Phase 2, invest in a custom Hinterland style only if it becomes a brand priority.
- **Tile provider is a critical dependency.** If the chosen provider (Stadia, Protomaps, self-host) goes down or changes terms, the map goes blank. Mitigation: MapLibre style specs are portable across providers — swapping providers is a one-line config change, not a rewrite.
- **Loss of Google SDK features we don't use.** Directions, indoor maps, 3D buildings, traffic overlays. None of these are on Hinterland's roadmap in any phase. No real cost.

### Neutral

- **This ADR does not pick a tile provider.** Stadia Maps, Protomaps, MapTiler, and self-hosting are all viable. The choice is Phase 2 work and can be made on price and reliability data we'll have by then.
- **The split pattern may need revisiting if Google changes Geocoding pricing.** Google raised Maps API prices significantly in 2018; another such move could flip the cost calculus. With caching, we can absorb a 3–5x price increase; larger than that warrants reconsideration.

## Alternatives considered

### All-in on Google Maps Platform (SDK + data APIs)

**Rejected.** This is the "obvious" choice that most tutorials point to, and it's the wrong one for this app. Per-map-view pricing penalizes the exact engagement we're trying to drive (territory map opens). The SDK's UI affordances (Street View tap-through most prominently) are wrong for a kid-facing app. On-screen attribution requirements clutter a precious UI. And we gain nothing in Hinterland's feature set that OSM can't provide.

Would become the right call if Google offered a "kids app tier" with SDK feature restrictions and pricing that didn't punish engagement — neither exists.

### All-in on OSM / MapLibre (no Google APIs)

**Rejected.** OSM's Nominatim service can do geocoding, and Overpass can do Places-like queries, but both are rate-limited at levels that make them unusable for a production app without self-hosting. Self-hosting Nominatim means a PostGIS database that eats 500GB+ for a global dataset and needs weekly update ingests — directly contradicting ADR 0001's "no ops burden" premise. And the data quality delta vs Google is real for the US suburban/urban use case.

We could self-host for Phase 3+ if Google's data-API costs become a line item we care about. At Phase 1 scale, not worth it.

### Apple Maps / MapKit

**Rejected.** Cross-platform requirement (iOS, Android, web via Expo) is incompatible with MapKit's iOS-first posture. Apple has a web MapKit JS, but no Android story. Disqualified on portability alone.

### Mapbox GL (not MapLibre)

**Considered and rejected in favor of MapLibre.** Mapbox GL is the proprietary version MapLibre was forked from; it's higher-polish but has a free tier and per-view pricing on the paid tiers. For a greenfield project starting in 2026, MapLibre is the more defensible choice: identical rendering capabilities in the code paths Hinterland uses, genuinely open license, no pricing-change risk, larger community contributor base post-fork. If Mapbox ships a compelling feature MapLibre lacks (the main candidates are realtime traffic and 3D terrain at polish-level), revisit — but neither is on our roadmap.

## Follow-ups

- Choose a tile provider before Phase 2 begins; compare Stadia Maps, Protomaps (self-host possible), and MapTiler on pricing, style availability, and offline-bundle support. Tile provider choice becomes a small ADR of its own (0004 or 0005) when decided.
- Define the `GEO#` cache partition pattern explicitly in `docs/data-model.md` — it's not yet documented there. Add in the same PR as the first Geocoding integration (Phase 1 Week 5).
- Set CloudWatch budget alarms on the Google Maps Platform project: $10/month soft alert, $50/month hard alert. If we're hitting either, the caching isn't working as designed.
- Bundle an offline tile pack for the user's home region on first app launch (Phase 2). Scope: a 25km radius at zoom 10–14. Measure bundle size and adjust.
- Verify Google Maps Platform Terms of Service around kid-directed services and COPPA before any integration ships. If there's a restriction, this ADR is revisited; if Google allows it with specific configuration, document the required settings in `docs/runbook.md`.
