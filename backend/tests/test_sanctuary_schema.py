"""Metadata-only assertions for the Sanctuary state tables.

Mirrors `tests/test_db_foundation.py`: imports the models module to
register tables on `Base.metadata`, then asserts table names, named
constraints, named indexes, and the absence of any precise-location
columns. No Postgres, no Alembic upgrade -- pure schema introspection.
"""

from app.db import models  # noqa: F401
from app.db.base import Base

SANCTUARY_TABLES = {
    "sanctuary_zone_state",
    "sanctuary_elements",
    "sanctuary_observation_contributions",
    "sanctuary_events",
}

# Defensive: no Sanctuary table may store precise location. Matched as
# exact column names AND as substring contains for catch-all coverage
# (a future contributor copy-pasting Observation columns into a Sanctuary
# model would otherwise leak place_name / rounded_lat / etc.).
FORBIDDEN_LOCATION_COLUMN_NAMES = {
    "lat",
    "lng",
    "latitude",
    "longitude",
    "geohash",
    "geohash4",
    "coords",
    "gps",
    "place",
    "place_name",
    "neighborhood",
    "city",
    "address",
    "location",
    "rounded_lat",
    "rounded_lng",
}

FORBIDDEN_LOCATION_SUBSTRINGS = ("lat", "lng", "geo", "addr", "place", "coord")


def test_sanctuary_tables_are_registered() -> None:
    table_names = set(Base.metadata.tables)

    assert SANCTUARY_TABLES.issubset(table_names)


def test_sanctuary_zone_state_constraints_exist() -> None:
    table = Base.metadata.tables["sanctuary_zone_state"]
    constraint_names = {c.name for c in table.constraints}
    index_names = {i.name for i in table.indexes}

    assert "uq_sanctuary_zone_state_user_zone" in constraint_names
    assert "ix_sanctuary_zone_state_user_depth" in index_names


def test_sanctuary_elements_constraints_exist() -> None:
    table = Base.metadata.tables["sanctuary_elements"]
    constraint_names = {c.name for c in table.constraints}
    index_names = {i.name for i in table.indexes}

    assert "uq_sanctuary_elements_user_zone_element" in constraint_names
    assert "ck_sanctuary_elements_element_type" in constraint_names
    assert "ix_sanctuary_elements_user_zone" in index_names


def test_sanctuary_observation_contributions_pk_is_observation_id() -> None:
    """The contribution PK is the idempotency gate -- it MUST be observation_id."""
    table = Base.metadata.tables["sanctuary_observation_contributions"]
    pk_column_names = {c.name for c in table.primary_key.columns}

    assert pk_column_names == {"observation_id"}, (
        f"sanctuary_observation_contributions PK must be exactly "
        f"{{'observation_id'}} (the idempotency gate); got {pk_column_names}"
    )


def test_sanctuary_events_constraints_exist() -> None:
    table = Base.metadata.tables["sanctuary_events"]
    constraint_names = {c.name for c in table.constraints}
    index_names = {i.name for i in table.indexes}

    assert "ck_sanctuary_events_event_type" in constraint_names
    assert "ix_sanctuary_events_user_created_at" in index_names


def test_sanctuary_tables_have_no_precise_location_columns() -> None:
    """No Sanctuary row may carry precise location. Verified by exact match
    AND substring scan so an accidental `place_name` or `rounded_lat` copy
    from `observations` is caught."""
    for table_name in SANCTUARY_TABLES:
        table = Base.metadata.tables[table_name]
        column_names = {column.name for column in table.columns}

        exact_hits = column_names & FORBIDDEN_LOCATION_COLUMN_NAMES
        assert not exact_hits, (
            f"Sanctuary table {table_name} must not store precise location; "
            f"found forbidden columns: {sorted(exact_hits)}"
        )

        substring_hits = {
            col
            for col in column_names
            if any(token in col for token in FORBIDDEN_LOCATION_SUBSTRINGS)
        }
        assert not substring_hits, (
            f"Sanctuary table {table_name} has columns matching a "
            f"location-leak substring: {sorted(substring_hits)}"
        )


def test_sanctuary_does_not_touch_memberships() -> None:
    """Leaderboard counters live on `memberships`. No Sanctuary table may
    reference `memberships` or duplicate its counter columns."""
    membership_counter_names = {"observation_count", "dex_count", "rarest_tier"}

    for table_name in SANCTUARY_TABLES:
        table = Base.metadata.tables[table_name]
        # The Sanctuary zone-state observation_count is intentional and
        # zone-scoped (its unique key is per-zone), not the global membership
        # counter -- skip that single case and check every other column.
        if table_name == "sanctuary_zone_state":
            continue
        column_names = {c.name for c in table.columns}
        overlap = column_names & membership_counter_names
        assert not overlap, (
            f"Sanctuary table {table_name} must not duplicate membership "
            f"counter columns; found: {sorted(overlap)}"
        )

        # No FK from any Sanctuary table to `memberships`.
        for column in table.columns:
            for fk in column.foreign_keys:
                assert "memberships" not in fk.target_fullname, (
                    f"Sanctuary table {table_name}.{column.name} must not "
                    f"reference memberships; got FK to {fk.target_fullname}"
                )
