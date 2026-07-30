# ADR 0017: Group ownership and multi-family privacy

- **Status:** Accepted
- **Date:** 2026-07-18
- **Deciders:** Product owner and implementation agents
- **Related:** ADR 0010, ADR 0015, ADR 0016

## Context

The adult web experience was originally described as a Classroom even though
the persisted resource and API are already a `Group`. The first intended use is
now a small group of friends and their parents. Teacher use remains possible in
the future, but its onboarding, consent, and administrative requirements have
not been decided.

Treating every adult in a shared group as a managing adult would expose one
family's child handoff, photos, review work, observations, or deletion controls
to unrelated parents. Treating the group creator as every child's guardian
would create the same privacy failure. A multi-family group therefore needs a
separate group-administration boundary and parent-child authority boundary.

The current Play Internal v12 build is single-family W1 evidence. Its required
low-memory-device, adult dry-run, received-alert, supervised-family-session,
post-session audit, and go/no-go evidence must be completed and archived before
this decision is merged or deployed. A later Groups build requires a new
protected promotion, higher Play version, and repeated device evidence.

## Decision

### Group administration

The adult who creates a group is its sole **group owner**. The owner may rename
or archive the group, manage adult invitations, and remove adult memberships.
Group ownership does not grant authority over another parent's children.

Adults may belong to multiple groups. A child may have only one active group
membership in this release.

### Parent-child authority

Each parent manages only children whose canonical `users.parent_user_id` is
that parent's user ID. A joined parent may create their own child in the group
after satisfying the current consent contract. Child creation always records
the requesting parent as the canonical parent.

Only that canonical parent may issue or reissue the child's handoff, manage the
child account, correct or delete the child's observation through the supported
compensating workflows, or exercise parent-authorized access to the child's
data. The child may still correct their own identification. Parent-authorized
deletion is a tombstone plus durable photo revocation and rebuild, never a
direct row delete or piecemeal counter decrement.
Neither group ownership nor ordinary adult membership grants access to another
family's QR codes, photos, observations, review items, corrections, deletion,
queue state, or private child metadata.

Removing a parent deactivates that parent's membership and the active group
memberships of their children, and revokes sessions scoped to the removed
group. It does not delete their accounts, observations, or photos. The parent
may later use the minimized parent-only owned-child inventory to place those
children in another group and issue new handoffs. The inventory returns only
the caller's child ID, display name, age band, and active group ID (or null);
it contains no membership IDs, counters, or peer records.

Each child membership carries a monotonically increasing session version in
handoff and session JWTs. Leaving or reactivating a membership increments that
version, and every authenticated kid request compares it with the active
database row. Tokens from the v12 compatibility release that lack the claim
are treated as version 1 only; after the first lifecycle transition they can
never become valid again.

Co-guardianship and delegated management require a separate consent and
privacy decision. They are not inferred from group membership.

### Adult invitations

Reusable six-character join codes are retired from the shared-groups product
surface. The compatibility column may remain during migration, but it is not
returned to clients or accepted after shared Groups is enabled. Creating the
first adult invitation stamps an irreversible per-group enablement marker, so
turning a runtime feature flag off later cannot reactivate that group's legacy
join code.

The group owner creates a one-time adult invitation link. The server stores
only a high-entropy token digest. The invitation expires after 72 hours, is
revocable before use, and is redeemed atomically by the first authenticated
parent. Same-user replay returns the existing membership; another account or a
changed request receives a conflict. Raw tokens are never listed, logged, or
placed in telemetry. Automated email delivery is out of scope; the owner copies
the link through a private channel.

### Roles and presentation

The internal `teacher` role remains for data and authentication compatibility,
but it grants no capability merely by being present. Educator-specific signup,
class rosters, bulk import, welcome sheets, and teacher review are deferred.

Children do not receive the adult roster or peer names, age bands, individual
progress, join material, observations, or photos. A future social/group feature
must use a separately reviewed aggregate-only DTO.

The canonical adult route is `/groups`. `/classroom` redirects for one
compatibility release. The product uses neutral language such as Groups,
Create group, Add child, and Invite parent.

### Authorization and rollout

Authorization derives from explicit group ownership, adult membership, and the
canonical parent-child relationship. Photo and review access never derives
from shared group membership alone. Reviewer authority remains an explicit
system assignment. This change does not create such an assignment: the current
adult review API remains canonical-parent-only, and neither `teacher` nor group
membership is a substitute.

Invitation creation and redemption are guarded by the
`shared_groups_enabled` capability. It is default-off in every environment.
Migrations and server authorization land before the parent UI is enabled. The
capability remains false until the current v12 evidence is complete and the
cross-family access matrix, account-isolation tests, and a canary group pass.
For a shared-Groups release, the migration/API deployment must reach the exact
main commit before the manually dispatched parent-web deployment; the
protected promotion then re-verifies that same commit before any canary flag is
set.

Disabling the rollout flag blocks new invitation creation and redemption. For
a group whose durable sharing marker is already set, it deliberately does not
block the owner's safety controls: existing invitation metadata remains
listable, pending invitations remain revocable, and adults remain removable.
The legacy join code remains retired in either state.

## Consequences

- Friends and their parents can share group membership without sharing private
  child data.
- The group owner can organize the group but cannot act as another family's
  guardian.
- Joined parents can add and manage their own children.
- Existing teacher data remains readable, but no unsupported teacher product is
  advertised or authorized.
- Parent removal requires transactional membership/session cleanup rather than
  child-data deletion.
- Group-wide leaderboards or peer detail are not available to children without
  a later privacy decision.

## Rejected alternatives

- **All adults manage all children:** rejected because membership alone is not
  consent for cross-family photo, review, account, or deletion access.
- **The group owner manages every child:** rejected because organizing a group
  does not establish guardianship.
- **Terminology-only rename with permanent join codes:** rejected because it
  would advertise multi-family use while retaining unsafe and non-revocable
  admission semantics.
- **Remove the teacher role now:** rejected because it is a compatibility
  migration unrelated to the group-first product decision.
