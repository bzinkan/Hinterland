# The Sanctuary (open-world layer) — moved

This doc has been superseded by [`docs/sanctuary.md`](sanctuary.md).

The product intent and the Phase 2 placement are unchanged. The
single substantive correction: the data layer is **Postgres only**
under ADR 0005, so the legacy DynamoDB single-table key language
(`USER#<id>` / `WORLD#<...>` SK) that this file previously described
is no longer accurate. The `WORLD#` phrasing kept in `AGENTS.md`'s
Phase 2 candidate list is conceptual only; the real persistence
lands as new SQLAlchemy + Alembic tables (indicative names
`sanctuary_zone_state`, `sanctuary_unlocks`, `sanctuary_content`) per
the schema PR in `docs/sanctuary.md` section 12.

For everything else — product promise, seven zones, deepening
thresholds, reward types, dispatcher integration, mobile UX, safety
boundaries, phase plan — see
[`docs/sanctuary.md`](sanctuary.md).
