"""Date-based seasonal selector for the Sanctuary.

The Sanctuary read endpoint (``GET /v1/sanctuary/me``) tags each response
with the current meteorological season so the mobile screen can render a
calm seasonal tint without depending on a future ``SeasonHandler`` or any
external API. The selector is intentionally simple: it converts the
current server date into one of four ``Season`` literals already declared
in ``app.models.sanctuary``.

Known limitation -- Northern Hemisphere assumption
--------------------------------------------------

The Sanctuary's existing privacy posture (see ``docs/sanctuary.md`` and
``docs/risks/0007-google-play-families-location-policy.md``) forbids
using precise location at render time and limits any region signal to the
coarse ``geohash4`` cell already used elsewhere. This module therefore
hardcodes a Northern Hemisphere meteorological calendar:

    spring : March 1     - May 31
    summer : June 1      - August 31
    autumn : September 1 - November 30
    winter : December 1  - February 28 / 29

For a Southern Hemisphere user the displayed season will be inverted
relative to their lived experience. This is a documented MVP gap; the
Phase 3 ``SeasonHandler`` work will swap this helper for one that reads
the coarse geohash4 hemisphere and picks the correct calendar. Until
that lands, the value is still useful as a visual tone -- a kid in
Sydney seeing "autumn" tint in May is misaligned but never harmful, and
all kid-facing copy still comes from authored content.

The helper takes ``today`` as an argument (no implicit ``date.today()``
call) so callers control the clock for tests and so the planner-purity
rule from ``docs/sanctuary.md`` Section 9 ("no ``datetime.now`` /
``random`` / ``uuid.uuid4`` reads") continues to hold inside the planner
itself; only the route layer reaches for the clock.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

Season = Literal["spring", "summer", "autumn", "winter"]


def current_season(today: date) -> Season:
    """Return the Northern Hemisphere meteorological season for ``today``.

    Boundaries are inclusive of the named month boundaries:

        Mar 01 .. May 31 -> spring
        Jun 01 .. Aug 31 -> summer
        Sep 01 .. Nov 30 -> autumn
        Dec 01 .. Feb 28 / 29 -> winter
    """
    month = today.month
    if 3 <= month <= 5:
        return "spring"
    if 6 <= month <= 8:
        return "summer"
    if 9 <= month <= 11:
        return "autumn"
    return "winter"
