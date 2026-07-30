# Onboarding

This document is the active Azure/Entra onboarding contract. Historical
username/PIN, Firebase, teacher welcome-sheet, bulk class import, and
teacher-created-child designs are not implemented product behavior.

Group ownership and cross-family privacy are governed by
[ADR 0017](adr/0017-group-ownership-and-multi-family-privacy.md).

## Release hold

The current Play Internal v12 single-family W1 evidence must be completed and
archived before Group-first changes merge or deploy. The remaining gates are a
physical Android device with at most 4 GB RAM, the accepted adult dry run,
actual alert receipt, one supervised family session, its post-session audit,
and explicit go/no-go and continuation decisions.

Any later Groups build requires a new protected server promotion, a higher Play
version, and repeated device/account-isolation evidence.

## Product language

The adult experience is **Groups**, not Classroom.

- Canonical route: `/groups`.
- `/classroom` redirects for one compatibility release.
- Adult actions use **Create group**, **Add child**, **Invite parent**, and
  **New sign-in QR**.
- Kid-facing copy may continue to say “kid”; adult management copy says
  “child”.
- Educator-specific signup, class rosters, welcome sheets, school metadata,
  and bulk import are deferred until teacher requirements are approved.

The internal `teacher` role remains readable for compatibility. It grants no
capability merely because the role exists.

## Roles and authority

### Group owner

The adult who creates a group becomes its owner. The owner manages:

- group name and archival;
- adult invitation creation and revocation; and
- removal of adults from the group.

The owner manages only their own children. Organizing a group does not make the
owner another family's guardian and does not grant access to another family's
QR codes, photos, observations, reviews, corrections, deletion, or private
child metadata.

### Joined parent

A joined parent may view the group and add/manage only their own children. The
parent must satisfy the current consent contract before creating a child. Child
creation records the requesting adult as the child's canonical
`parent_user_id`.

Joined parents cannot rename/archive the group, manage adult membership, or
access another family's private data.

### Child

A child has one active group in this release and uses only their own Journal,
Dex, Expeditions, Sanctuary, and Observation data. Children do not receive join
material, an adult roster, peer names or ages, individual progress, peer
observations, or peer photos. Adults may belong to multiple groups.

## First parent and group creation

1. The adult opens `/consent` in a fresh browser context.
2. The adult reads the current policy, records consent, and keeps the
   browser-bound setup proof in that tab. The API stores only its digest.
3. The adult continues through Microsoft Entra External Identities in the same
   tab and returns through `/auth/callback`.
4. The parent web app resolves the canonical adult through `/v1/me`. Existing
   valid consent is reused; a returning parent is not required to consent
   again.
5. The adult lands on `/groups`. An anonymous or expired session shows an
   explicit sign-in action rather than an indefinite loading state.
6. The adult creates a group and becomes its owner.
7. The adult selects **Add child**. The server rechecks current consent, creates
   the child with the requesting adult as canonical parent, and creates the
   child's sole active group membership.
8. The parent web app renders a short-lived, single-use
   `hinterland.kid-handoff.v1` QR. The native app exchanges it at
   `POST /v1/auth/kid-exchange` for the kid session.

The raw browser setup proof, OAuth callback material, QR payload, access token,
and kid token are never logged or persisted as onboarding evidence.

## Returning parent

The adult opens `/groups` and signs in with Entra when no valid local session is
present. Successful sign-in returns to `/groups`. A disabled query must not be
presented as loading forever. Expired or invalid sessions clear protected group,
child, QR, and request presentation state before sign-in.

If the parent needs to restore their child on a new device, they select **New
sign-in QR** for their own child. The API rechecks the canonical parent-child
relationship and active group membership before minting a fresh 15-minute
handoff. The handoff remains only in modal memory and is cleared on completion,
expiry, account/group replacement, or unmount.

## Inviting another parent

Shared Groups is hidden and server-denied until the
`shared_groups_enabled` capability is explicitly enabled for an approved
canary. The capability is default-off and must remain off until the v12 hold is
closed.

1. The owner selects **Invite parent**.
2. The API returns one high-entropy invitation link once and stores only its
   digest. The link expires after 72 hours.
3. The owner copies the link through a private channel. Hinterland does not send
   invitation email in this release.
4. The recipient signs in as a parent and redeems the invitation. Redemption
   is atomic: the first authenticated parent joins; same-user replay is
   idempotent; a different account receives a conflict.
5. Joining grants adult group membership only. It grants no authority over an
   existing child.
6. After satisfying consent, the joined parent may add and hand off their own
   child.

The owner can revoke an unused invitation. Invitation lists show bounded
metadata such as state and expiry, never the raw token or digest. Reusable
six-character join codes are not exposed or accepted once shared Groups is
enabled.

If the sharing rollout flag is disabled, new links and redemption stop, while
owners of already-shared groups retain revocation and adult-removal controls.
This is a safety path, not a way to re-enable legacy join codes.

## Removal and recovery

When the owner removes a parent, the server deactivates that parent's group
membership and the active memberships of that parent's children, then revokes
sessions scoped to the removed group. It does not delete accounts,
observations, or photos. The removed parent may place their children in another
group and issue new handoffs. `GET /v1/groups/owned-children` supplies the
parent-only recovery inventory; ungrouped children have `active_group_id=null`
and the response contains no membership IDs, counters, or peer records.

Co-guardians, delegated child managers, and delegated reviewers require a
separate consent/privacy design. They are not inferred from group membership.

## Privacy and failure rules

- Ordinary adult group membership never authorizes photo or review access.
- Same-group peer children never receive each other's records or photo URLs.
- Account changes synchronously clear group, child, QR, queue, query, image, and
  draft presentation state.
- Invitation tokens, QR payloads, OAuth material, child text, photos, and raw
  coordinates never enter logs or screenshots.
- Consent, invitation redemption, child creation, and handoff failures fail
  closed and show a child-safe/adult-actionable error with a bounded request ID.
- There is no kid self-registration, chat, direct messaging, public discovery,
  or kid-to-kid free text.

## Deferred educator design

Teacher onboarding is not a variation of parent onboarding. Before it is
enabled, a separate product/privacy decision must define school authority,
parental consent, roster visibility, child transfer, review delegation,
teacher verification, retention, and offboarding. Until then, UI and marketing
must not promise classroom administration.
