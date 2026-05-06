# ADR 0001: Single-table DynamoDB

- **Status:** Superseded by [ADR 0005](0005-gcp-target-architecture.md) on 2026-05-05
- **Date:** 2026-04-22
- **Deciders:** Solo author
- **Supersedes:** —

> **Superseded.** ADR 0005 selects Cloud SQL for PostgreSQL as the operational datastore on GCP. The reasoning below remains useful as historical context for the ADR 0001 → 0005 trade — particularly the access-pattern enumeration in §Context, which still maps 1:1 onto the Postgres schema.

## Context

Dragonfly needs a primary data store for users, groups, memberships, observations, a per-user species Dex, expedition progress, a per-region rarity cache, a photo review queue, and job state. The app is serverless (Lambda + API Gateway), expected to stay under 10k DAU for the first year, and is built and maintained by one person.

The access patterns are well-enumerated and narrow (see `docs/data-model.md`):

- Point lookups by id (user, group, observation, species).
- Range queries within a known parent (a user's Dex, a group's members, a group's observations over time).
- A small number of secondary lookups: email → user, join code → group, user → groups they belong to, species → observations of that species within a group.

There are no cross-entity joins, no full-text search, no ad-hoc analytics on the hot path. Analytics, if they come, will happen offline against an exported data lake, not against the operational store.

Three operational constraints matter:

1. **Solo ops.** Any store that requires patching, VACUUMing, connection pooling, or read-replica failover management is a tax paid every week forever.
2. **Cold starts.** Lambda + RDS requires either RDS Proxy or careful connection lifecycle management. Both add moving parts.
3. **Cost at zero.** The app will have weeks with a dozen users during early Phase 1. Paying $50/month for an idle RDS instance is a distraction that compounds.

## Decision

Use **a single DynamoDB table named `Dragonfly`** with a composite primary key (`PK`, `SK`) and two global secondary indexes (`GSI1`, `GSI2`), in on-demand billing mode. Store every entity type in that one table using prefix-based keys (`USER#<id>`, `GROUP#<id>`, `OBS#<ts>#<id>`, etc.).

The concrete schema is in `docs/data-model.md`; it is normative and must stay synchronized with the CDK definition in `infra/stacks/data_stack.py`.

## Consequences

### Positive

- **Zero ops at our scale.** No instances, no patches, no connections, no backups to configure. Point-in-time recovery is a one-line CDK setting.
- **Pay-per-use matches a pre-PMF app.** On-demand pricing means bill tracks usage; an idle dev environment costs cents per month.
- **Sub-10ms reads on every supported access pattern.** Because every pattern was designed against the key schema (not bolted on), there are no scans in the hot path.
- **Transactional writes across multiple entity types in one API call.** `TransactWriteItems` gives us atomic observation + membership counter + Dex entry in one round trip, no two-phase commit ceremony.
- **Schema evolution is cheap.** New attributes on existing rows require no migration. New entity types require no DDL — just a new key prefix.
- **Local dev parity is perfect.** DynamoDB Local has the same API as cloud DynamoDB. "Works on my laptop" genuinely means "will work in Lambda."

### Negative

- **Key design is forever.** Renaming `USER#` to `U#` is a full-table rewrite. The prefixes in `db/keys.py` are committed to early and changed only under duress.
- **Ad-hoc queries are expensive or impossible.** Anything not on the access-pattern list (e.g. "all observations in March where species was a plant") requires either a new GSI, a full scan, or an offline export. This is accepted: analytics go to a separate pipeline.
- **No server-side joins.** The app does joins in memory when needed (rare). For observation-with-species-name, we denormalize the species name onto the observation row.
- **GSI write amplification.** Every write to an observation hits the base table plus GSI1 and GSI2 (since observation rows populate both). Write cost is ~3x per observation vs a single-index design. Accepted because reads dominate and GSI reads are critical to leaderboards and species-wide lookups.
- **Unfamiliar mental model.** Anyone onboarding from a relational background has to learn single-table design. At team size 1 this doesn't matter; it's a cost noted for the future.
- **Hot partition risk at scale.** A viral group with thousands of members could push a single `GROUP#<id>` partition past 3000 RCU/s. Mitigation: not a real risk under 10k DAU; when it becomes one, shard the group key (`GROUP#<id>#<shard>`) and aggregate in-app. Re-evaluate at 5k DAU.

### Neutral

- **Observability is different.** DynamoDB's "slow query log" is CloudWatch Insights on the consumed capacity metrics, not `pg_stat_statements`. Equivalent information, different tool.
- **No SQL.** Some developers miss it. The author does not.

## Alternatives considered

### PostgreSQL on RDS

**Rejected.** Gives us familiar querying and joins, but: (a) cold-start connection management with Lambda is a genuine operational problem requiring RDS Proxy or a connection pooler, (b) an idle db.t4g.micro is $12–15/month in year one for zero value, (c) patching, minor version upgrades, and failover events are a recurring interruption that a solo dev cannot absorb well, (d) none of our access patterns actually need joins.

Would become the right choice if the app grew ad-hoc query needs (e.g. a teacher dashboard with arbitrary filters on student observations). When that happens, the correct move is to add Postgres *alongside* DynamoDB for the dashboard workload, not to replace the operational store.

### Multi-table DynamoDB (one table per entity)

**Rejected.** Feels more relational-friendly, but sacrifices DynamoDB's one real advantage: atomic multi-entity writes on the same partition. Our observation submission writes three entities (observation row, membership counter, Dex entry) in one transaction; with a multi-table design these would cross table boundaries and require `TransactWriteItems` across tables, which works but adds complexity and cost. Multi-table also multiplies IAM policies, backup configs, and alarms for no access-pattern gain.

### DocumentDB / MongoDB-compatible

**Rejected.** Document semantics fit the data fine, but cost starts at ~$200/month for the smallest cluster, and we'd inherit MongoDB's operational model (failovers, elections) without getting cloud-native scale-to-zero. No advantage over DynamoDB at our scale.

### Supabase / Neon / managed Postgres

**Considered.** These address the ops objection to RDS (managed, serverless-style, scale to zero). Neon in particular has genuinely good Lambda integration via HTTP. The case for them is real. They lose on two dimensions: (a) the access patterns still don't want joins, so we'd be using Postgres as a key-value store, which is strictly worse than DynamoDB at being a key-value store; (b) we add a vendor outside AWS to our critical path. Revisit in 18 months if DynamoDB's constraints start biting.

### Single DynamoDB table — the version we picked

**Accepted.** Best fit for the access patterns, the operational constraints, and the scale trajectory. The downsides (key rigidity, no ad-hoc queries) are real but aligned with the design already captured in `docs/data-model.md` — none of them are surprises, all of them have planned escape hatches (export-based analytics, additional GSIs, sharding), and none compromise the happy path.

## Follow-ups

- Add CloudWatch alarms on `ConsumedWriteCapacityUnits` per-index once Phase 1 ships, so the first sign of GSI write pressure is not a bill surprise.
- Document the shard-a-hot-group playbook in `docs/runbook.md` before it's needed (low urgency; Phase 2).
- Revisit this ADR at 5k DAU or on any sustained month where DynamoDB spend exceeds $200 — either triggers a real cost/benefit recomputation.
