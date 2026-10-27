# ADR 0017: Hintergrove fresh AWS and Unity build

- **Status:** Accepted for the Hintergrove successor; implementation pending.
- **Date:** 2026-10-27.
- **Decider:** Brian Zinkan.
- **Related:** [Interactive first playable](../hintergrove-first-playable.md), ADR 0010, ADR 0012, ADR 0013.
- **Scope:** New application foundation. Existing deployed code and infrastructure are not changed by this documentation update.

## Context

Brian chose a fresh start on AWS with no migration of the existing application, then selected **Hintergrove** as the new product name. The sanctuary should be a compact Unity scene with cohesive 3D art and animated wildlife.

The first product loop needs enjoyable interaction before and between outdoor observations. Brian approved five features: one responsive clearing, three distinct wildlife characters, one short search challenge, a few arrangeable objects, and one real outdoor observation that produces a strong visual reveal.

The existing repository remains named Hinterland and contains an Azure application and a Skia renderer. It now also records the successor plan so implementation can begin from explicit decisions.

## Decision

### Fresh foundation

Build Hintergrove in a clean sibling workspace or new repository, with `hintergrove-aws` as the proposed local name. Initialize source control and fresh agent/build instructions there. Establish an actual available remote destination separately.

No old account, photo, database, infrastructure, configuration, mobile identity, or endpoint migration is required. Existing code is optional reference material; do not reset its history, delete it, or shut down services as a side effect of starting the successor.

### Proposed architecture

| Component | Responsibility |
| --- | --- |
| Expo and React Native | Mobile navigation, adult-managed child sessions, camera, durable offline capture, Journal, accessible views, and Unity host |
| Unity | Full-screen 3D clearing, camera, wildlife animation, touch interactions, and presentation of authorized scene state |
| FastAPI on ECS Fargate | Authenticated API, authorization, observation validation, and permanent progression |
| RDS PostgreSQL | New application records, ownership, observations, and earned state |
| Private S3 | Staged and processed photos with controlled temporary access |
| Cognito | Adult authentication; application-managed child handoff/session is a separate flow |
| Supporting AWS services | ECR, IAM, Secrets Manager, CloudWatch, and Terraform; SQS and a worker when background processing is implemented |

Keep Hintergrove resources and permissions separate from SchoolPilot. Verify region, account access, credit eligibility, and cost before provisioning. Unity renders on the device and receives no AWS credentials or independent login/data layer.

Validate the Expo/Unity native integration early, including entry, exit, camera recovery, memory, and account replacement. Choose exact supported tool/package versions during that spike. Native engine changes require native builds.

### First playable

Use [the interactive first-playable specification](../hintergrove-first-playable.md) as the approved product scope.

The robin search and layout preferences are virtual play state. Real Journal observations and observation-derived habitat unlocks remain grounded in actual findings. A server-accepted broad plant observation can unlock the one patch without requiring an exact species name.

The starting robin, squirrel, and butterfly are illustrated characters. A later illustrated behavior represents a possible ecological relationship and does not establish that the child observed that species or interaction.

### Relationship to earlier decisions

ADR 0010's Azure platform choice, ADR 0012's Skia/art pipeline, and ADR 0013's Hinterland identity continue to describe the existing application. They are not constraints on the new Hintergrove foundation.

This decision reopens platform, renderer, naming, and fresh-schema choices for the successor only. It does not add a second cloud runtime to the old application or claim the new system already exists.

Keep durable/idempotent observation saving, private child data, adult-managed sessions, account isolation, and no child-facing runtime LLM calls as product requirements in the new build. Save observation records independently of slow identification or photo-review services.

## Consequences

- Prove a small standalone Unity interaction slice before a large backend or content catalog.
- Validate native integration before substantial art production.
- Use properly licensed, cohesive models, materials, rigs, and animations, with recorded provenance.
- Implement a new minimal AWS API and schemas when the real-discovery slice begins.
- Keep virtual achievements and personalization separate from evidence-based progression.
- Record device evidence and playtest observations; documentation approval is not implementation or release evidence.
- Asset purchases, billable provisioning, service shutdown, repository renaming, and public release are separate actions from this planning update.
