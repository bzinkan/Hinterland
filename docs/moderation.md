# Photo Moderation

Moderation is asynchronous and never determines whether the kid-facing
Observation save succeeds. W1 uses explicit private NoOp processing; closed
beta may enable Azure AI Content Safety only after the complete worker, review,
rebuild, retention, and alert gates pass.

The authority for this contract is
[ADR 0015](adr/0015-observation-finalization-and-derived-state-rebuild.md).

## Lifecycle At A Glance

```text
reserved raw upload
        |
        | observation finalization transaction
        v
attached canonical JPEG + pending moderation outbox
        |
        +-- W1 noop --------------------------> pilot_private -> 7-day purge
        |
        +-- Azure Content Safety safe --------> clean
        |
        +-- Azure Content Safety flagged -----> quarantine -> adult review
        |                                             | approve -> clean
        |                                             ` reject  -> tombstone + rebuild
        `-- unavailable/malformed -----------------> retry / failed / DLQ
```

Blob creation is not a moderation event. Direct BlobCreated/Event Grid
delivery must remain absent. Only a committed `moderation_outbox` row may
produce Service Bus work.

## Attachment And Moderation Are Separate

Photo attachment is `reserved|attached|deleted`. Observation moderation is
`pending|processing|clean|quarantine|pilot_private|rejected|failed`.
`moderation_source` records `none|noop|azure|adult` plus the policy version.

This prevents an upload from becoming safety-approved merely because bytes
exist or an observation references them.

## Upload Finalization

Presign reserves `pending/uploads/<photo_id>.jpg` and returns the provider
headers mobile must send verbatim, including `x-ms-blob-type: BlockBlob`.

Before observation insert, the API reads Blob properties, rejects missing or
oversized bytes, verifies the object did not change during download, checks
JPEG magic/decode and decompression limits, requires dimensions from 50 through
1600 pixels, strips metadata, re-encodes a canonical JPEG, calculates SHA-256,
and writes immutable bytes under `pending/finalized/`.

The canonical object metadata and attachment happen in the same logical
finalization flow as the observation/idempotency/outbox transaction. Raw bytes
are deleted best effort after commit; retention removes true orphans after 24
hours.

### Legacy cutover

The additive migration registers attached legacy pending observations in the
outbox before Event Grid is removed. The relay requires an attached, verified
canonical photo, so those rows cannot publish raw `pending/<photo_id>.jpg`
bytes. `admin.observation_legacy_reconcile` runs before the relay is provisioned,
again after API cutover, and on a temporary schedule for the compatibility
release. It verifies/re-encodes legacy bytes, fills migration fields, removes
any raw coordinates written by the old revision, and then releases the outbox
row. Invalid or missing bytes fail closed to rejection plus deterministic
rebuild.

## Outbox And Worker Contract

The relay:

1. reads only committed `pending` or retryable `failed` rows;
2. sends an envelope containing the exact observation, photo, container, and
   canonical object;
3. uses the observation ID as the Service Bus message ID; and
4. records enqueue success, retry context, or terminal/DLQ state.

The worker atomically leases one row. Duplicate delivery or lease expiry is
harmless. It validates the canonical JPEG again before provider egress.

Azure Content Safety success is accepted only when exactly one valid severity
exists for each expected category: Hate, SelfHarm, Sexual, and Violence.
Missing, duplicate, partial, unknown, or malformed results fail closed, retry,
and eventually DLQ; they never become clean.

## Object Moves

For clean/quarantine transitions the worker:

1. writes the destination without overwrite;
2. verifies destination byte length and SHA-256;
3. commits photo, observation, review, and outbox state; and
4. deletes the source best effort after commit.

Never delete the source immediately after starting an asynchronous Azure copy.
Use a synchronous server-side transfer or verify copy completion first.

## Signed Photo Access

The server enforces status and relationship on every signed GET. Container
privacy alone is insufficient.

| State | Owner child | Peer child | Authorized reviewer | Managing adult |
|---|---:|---:|---:|---:|
| `clean` | yes | no | yes | yes when group-authorized |
| `quarantine` | no | no | yes for their group | yes when reviewing |
| `pending`, `pilot_private`, `failed` | no | no | no | no |
| `rejected`, deleted | no | no | no | no |

Same-group membership never grants child-to-child photo access. Signed URLs are
not logged or cached across canonical-user changes. Child signed URLs expire in
60 seconds; mobile treats them as stale after 40 seconds and keeps a 10-second
request safety margin. An active `photo_revocations` row denies a fresh URL even
before storage relocation or the rejection transaction is complete.

## Child Presentation Contract

Journal/detail APIs derive one fail-closed child state:

| Child state | Exact meaning |
|---|---|
| `clean` | Observation and Photo are both clean and no revocation is active |
| `pending` | Attached photo is awaiting moderation work |
| `processing` | Moderation work has been claimed |
| `pilot_private` | W1 NoOp completed; metadata is visible but photo bytes remain private |
| `adult_review` | Quarantined and awaiting/under authorized adult review |
| `failed` | Processing failed or lifecycle states disagree; metadata only |

Unknown combinations choose the most restrictive non-image state. Pending,
processing, pilot-private, adult-review, and failed records may appear as
metadata-only cards, but the client must not request their bytes. Rejected or
deleted records are absent from child list/detail reads.

## Clean Photo Revocation

Closed beta uses a durable, unique-per-photo revocation before a previously
clean photo is rejected:

1. claim/create the revocation row; new signed-URL requests now fail;
2. copy the clean source without overwrite into the restricted held/rejected
   prefix;
3. verify destination length and SHA-256;
4. synchronously delete the clean source so an already-issued SAS points to a
   missing object;
5. transactionally finalize Photo, Observation, review, and rebuild state; and
6. mark revocation succeeded.

Recovery treats destination-already-exists and source-already-gone as expected
idempotent states, verifies bytes again, and resumes safely. If storage succeeds
but the database transaction fails, privacy remains fail-closed because the
clean source is absent and URL issuance is blocked. Retry is bounded and a
terminal failure alerts. Mobile removes an open image on any non-clean status,
refreshes visible status every 30 seconds and on foreground, and clears the
signed-URL query. The operational bound is URL expiry/removal within 60 seconds
and active-screen removal within 30 seconds; pixels already decoded by the OS
cannot be recalled.

An authorized managing adult corrects a prior approval through
`POST /v1/review-queue/{review_id}/revoke`. This path accepts only an
`approved` review whose Photo and Observation are still clean, records the
revoking adult on the durable revocation, preserves the original approval
reviewer/time, and finishes the review as `revoked`. The revocation's review
foreign key is restrictive: review retention or cleanup cannot erase the
signed-URL deny gate.

## W1 NoOp Mode

NoOp records `pilot_private`, never `clean`, and moves the verified JPEG to a
dedicated private `pilot-private/` prefix for a safe seven-day Azure lifecycle
rule. It grants no signed URL and
creates no iNaturalist work. W1 also enforces independent false gates at route,
producer, consumer, replay, and manual-endpoint boundaries for both iNaturalist
CV and public submission.

The W1 transition is intentionally asynchronous but has a release SLO. The
deployed moderation-outbox relay and NoOp moderation consumer are both
scheduled once per minute (`* * * * *`); the consumer processes a bounded batch
of eight messages. A newly committed outbox row can therefore cross two
minute boundaries before the consumer commits `pilot_private`. The protected
Observation canary measures from the successful create response—not from the
first later status poll—and requires:

- the create response to report truthful `pending`;
- the API to first report `pilot_private` within 150 seconds;
- only `pending` or `processing` before that transition; and
- no resubmission to cause or observe the transition.

The canary records the backend-only duration as `moderation_transition_ms`.
This separates server scheduling/processing latency from presentation
reconciliation. While a visible Journal or detail record remains `pending` or
`processing`, mobile refetches it every five seconds in the foreground and
stops polling after a terminal presentation state. The protected-device bound
is 160 seconds from the persisted create response: the 150-second server SLO
plus two foreground polling intervals. Its Maestro flow captures one absolute
deadline as soon as the persisted response renders “This photo is being
checked.” Navigation consumes that same budget; the continuation-only
Journal/detail assertion waits only for the remaining time before requiring
“This photo is private during the pilot.” It must never tap Save again, render
a photo component, or show the photo helper.

Breaching either bound blocks W1 promotion. Do not make the image visible,
mark it clean, invoke iNaturalist, or bypass the outbox to reduce latency.

Pilot-private bytes are removed after seven days. W1 groups remain isolated
from beta leaderboards and are archived before closed-beta promotion.

## Adult Review And Deterministic Rebuild

Approve/reject/stale-review resolution uses a row lock or conditional
`pending -> resolved` update so exactly one actor wins.

- **Approve:** copy and verify the canonical photo into `observations/`, record
  `clean` with `moderation_source=adult`, commit, then delete source best effort.
- **Reject:** run the fail-closed verified revocation above, tombstone the
  observation, and transactionally queue a per-user rebuild. Do not perform
  piecemeal counter decrements.
- **Revoke approval:** use the explicit approved-to-revoked correction route;
  it does not weaken the one-winner `pending` approve/reject race.
- **Stale:** call the same rejection service; do not implement a second cleanup
  algorithm.

The rebuild shares the finalization user lock and replaces all derived state in
one transaction from accepted observations ordered by `(observed_at, id)`. It
regenerates membership counters, Dex, rarity, Expedition contribution gates,
Sanctuary state, handler ledgers, and persisted rewards. Expedition enrollment
times are preserved and celebrations are suppressed. Triggers coalesce and a
job retries five times before alerting as failed.

## Identification And iNaturalist Egress

Generic Observation PATCH does not mutate derived identification. A dedicated
revision-checked identification event queues the same rebuild. Catalog IDs use
the server's canonical name; manual text has no taxon ID; Unknown has neither.

Image CV is allowed only for a clean/adult-approved canonical photo and only
when enable, disclosure-approved, and benchmark-approved gates are all true.
Suggestions cache by canonical SHA-256 and model version. Pending,
pilot-private, quarantine, failed, rejected, and deleted states fail closed.

Public iNaturalist submission remains disabled for W1 and closed beta. It is a
separate consent and geoprivacy project.

## Retention

- unattached raw uploads and canonical orphans: 24 hours;
- W1 pilot-private photos: seven days;
- quarantined/rejected bytes: 90 days; and
- clean-photo/account deletion: governed by the reviewed privacy policy and
  erasure workflow.

Blob lifecycle handles safe prefix-wide rules, including the dedicated
`pilot-private/` seven-day prefix. Database-aware retention independently
enforces the pilot deadline, scans the complete `pending/` tree (including the
old flat prefix), and handles states that cannot be determined from a prefix
alone. A photo with an observation is never purged as an unattached reservation
merely because the old API left the new attachment column at its default.

## Closed-Beta Gate

Do not enable Content Safety until staging proves safe, flagged, unavailable,
malformed-response, duplicate-delivery, lease-expiry, destination-exists,
database-failure-after-copy, retry, and DLQ paths. Concurrent
approve/reject/stale races and deterministic first-find replacement rebuilds
must pass against real PostgreSQL. Alerts and lifecycle rules must be
synthetically verified before the 24-hour or 25-submission canary begins.
