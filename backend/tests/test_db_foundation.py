from app.db import models
from app.db.base import Base


def test_postgres_foundation_tables_are_registered() -> None:
    table_names = set(Base.metadata.tables)

    assert {
        "users",
        "groups",
        "memberships",
        "photos",
        "observations",
        "dex_entries",
        "expedition_progress",
        "review_queue",
        "ingest_runs",
        "job_state",
        "species_cache",
        "taxonomy_packs",
        "cv_suggestion_cache",
        "geo_cache",
        "rarity_cache",
        "expedition_content",
        "observation_idempotency",
        "moderation_outbox",
        "observation_handler_runs",
        "derived_state_rebuilds",
        "expedition_observation_contributions",
    }.issubset(table_names)


def test_submission_keys_stay_nullable_during_migration_first_rollout() -> None:
    """The previous API must keep inserting while 0014 runs before deploy."""
    assert models.Photo.__table__.c.submission_key.nullable is True
    assert models.Observation.__table__.c.submission_key.nullable is True


def test_first_find_and_ingest_idempotency_constraints_exist() -> None:
    dex_constraints = {
        constraint.name for constraint in Base.metadata.tables["dex_entries"].constraints
    }
    ingest_constraints = {
        constraint.name for constraint in Base.metadata.tables["ingest_runs"].constraints
    }

    assert "uq_dex_entries_user_taxon" in dex_constraints
    assert "uq_ingest_runs_source_run" in ingest_constraints
