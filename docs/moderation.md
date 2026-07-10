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
not logged or cached across canonical-user changes.

## W1 NoOp Mode

NoOp records `pilot_private`, never `clean`, and moves the verified JPEG to a
dedicated private `pilot-private/` prefix for a safe seven-day Azure lifecycle
rule. It grants no signed URL and
creates no iNaturalist work. W1 also enforces independent false gates at route,
producer, consumer, replay, and manual-endpoint boundaries for both iNaturalist
CV and public submission.

Pilot-private bytes are removed after seven days. W1 groups remain isolated
from beta leaderboards and are archived before closed-beta promotion.

## Adult Review And Deterministic Rebuild

Approve/reject/stale-review resolution uses a row lock or conditional
`pending -> resolved` update so exactly one actor wins.

- **Approve:** copy and verify the canonical photo into `observations/`, record
  `clean` with `moderation_source=adult`, commit, then delete source best effort.
- **Reject:** mark the photo deleted, tombstone the observation immediately,
  deny access, and transactionally queue a per-user rebuild. Do not perform
  piecemeal counter decrements.
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
