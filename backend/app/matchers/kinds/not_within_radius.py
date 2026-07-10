"""not_within_radius_of_existing matcher.

Equirectangular distance approximation -- accurate enough for the radii
the matcher accepts (1m to 10km, kid app scale). For larger ranges, a
proper haversine would matter, but equirectangular is faster and the
schema constraint keeps us in the safe band.
"""

from __future__ import annotations

import math

from app.matchers.context import MatcherInputs
from app.models.expedition import MatchNotWithinRadius

_EARTH_RADIUS_M = 6_371_000


def _approx_distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Equirectangular projection. Inputs in degrees, output in meters."""
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlat = lat2_rad - lat1_rad
    dlng = math.radians(lng2 - lng1)
    mean_lat = (lat1_rad + lat2_rad) / 2
    x = dlng * math.cos(mean_lat)
    y = dlat
    return math.sqrt(x * x + y * y) * _EARTH_RADIUS_M


def match_not_within_radius(spec: MatchNotWithinRadius, inputs: MatcherInputs) -> bool:
    """True if no prior observation is nearby and precise input is available.

    A four-character geohash is intentionally too coarse for a meter-radius
    decision. Returning ``False`` for W1 coarse/no-location observations avoids
    awarding progress from misleading geometry.
    """
    if inputs.obs_latitude is None or inputs.obs_longitude is None:
        return False
    for prior in inputs.user_prior_observations:
        if (
            _approx_distance_m(
                inputs.obs_latitude,
                inputs.obs_longitude,
                prior.latitude,
                prior.longitude,
            )
            < spec.radius_meters
        ):
            return False
    return True
