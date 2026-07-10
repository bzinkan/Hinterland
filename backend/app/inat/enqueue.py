"""Service Bus enqueue helper for the iNat-submit transactional outbox.

Risk 0002 closure: production iNat submission rides on a transactional
outbox + Service Bus queue (per the ADR-0010 Azure runtime model). The
producer side -- moderation worker clean path and review-queue approve
handler -- writes an `inat_submit_outbox` row in the SAME SQLAlchemy
transaction that flips `observations.moderation_status` to `clean`,
commits, then calls `enqueue_inat_submit()` here to send a message to
the `inat-submit` queue. The downstream consumer (Container App
worker, separate PR) dequeues and calls
`app.inat.submit.submit_observation_to_inat()` directly under
managed identity.

Authentication is `DefaultAzureCredential` -- in Container Apps that
picks up the user-assigned managed identity granted
`Azure Service Bus Data Sender` on the queue. Locally the credential
chain falls through to `az login` (or whatever else is configured)
and the enqueue should generally be a no-op during dev anyway.

Safe degradation
----------------

The helper deliberately never raises on the producer hot path. Failure
modes -- Service Bus not provisioned yet, transient network blip,
managed-identity misconfiguration, anything else -- return an
`InatEnqueueResult` with `success=False` and a short `reason`. The
caller MUST keep the outbox row in `pending` so the replay job picks
it up later. That preserves at-least-once semantics without losing
observations to a Service Bus outage during the rollout window where
infra (B3 in the risk-closure plan) hasn't landed yet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:  # pragma: no cover - import guard for type-only deps
    from app.core.config import Settings

log = structlog.get_logger()


@dataclass(frozen=True)
class InatEnqueueResult:
    """Outcome of one `enqueue_inat_submit()` attempt.

    `success=True` means the message was accepted by Service Bus and the
    outbox row should be flipped from `pending` to `enqueued`.
    `success=False` means the caller MUST keep the outbox row in
    `pending` so the 15-min replay job retries it. `reason` is a short
    machine-readable tag; full exception detail is logged in-module.
    """

    success: bool
    reason: str | None = None


async def enqueue_inat_submit(
    observation_id: str,
    *,
    settings: Settings,
) -> InatEnqueueResult:
    """Send `{ "observation_id": ... }` to the Service Bus iNat queue.

    Never raises. Returns success=False on any failure so the caller
    can decide whether to flip the outbox row or leave it pending.

    Reasons surfaced to the caller (for structured logging + tests):

    - ``not_configured`` -- `settings.service_bus_enabled` is False
      (empty namespace). Expected during the rollout window before
      Service Bus is provisioned (Stream B in the risk-closure plan).
    - ``import_failed`` -- the `azure-servicebus` SDK isn't importable
      (supply-chain regression). Production alarm material.
    - ``send_failed`` -- the send call raised. Covers transient
      network blips, auth misconfig, queue-not-found, throttling, etc.
      The full exception is logged at WARNING with stack info.
    """
    if not settings.inat_submit_enabled:
        log.info(
            "inat.enqueue.skipped_disabled",
            observation_id=observation_id,
        )
        return InatEnqueueResult(success=False, reason="disabled")

    if not settings.service_bus_enabled:
        log.info(
            "inat.enqueue.skipped_not_configured",
            observation_id=observation_id,
        )
        return InatEnqueueResult(success=False, reason="not_configured")

    # Lazy import keeps the dependency optional at import time so unit
    # tests of upstream modules don't have to pull in azure-servicebus.
    # The dev / local config rarely exercises this path; production
    # Container Apps obviously do.
    try:
        from azure.identity.aio import DefaultAzureCredential
        from azure.servicebus import ServiceBusMessage
        from azure.servicebus.aio import ServiceBusClient
    except ImportError as exc:  # pragma: no cover - install-time issue
        log.error(
            "inat.enqueue.import_failed",
            observation_id=observation_id,
            error=str(exc),
        )
        return InatEnqueueResult(success=False, reason="import_failed")

    payload = json.dumps({"observation_id": observation_id})

    try:
        credential = DefaultAzureCredential()
        try:
            client = ServiceBusClient(
                fully_qualified_namespace=settings.service_bus_namespace,
                credential=credential,
            )
            async with client:
                sender = client.get_queue_sender(queue_name=settings.service_bus_inat_queue)
                async with sender:
                    message = ServiceBusMessage(
                        payload,
                        # MessageId is the standard Service Bus dedup key;
                        # if a future requirement turns on dedup at the queue
                        # level the producer side is already shaped for it.
                        message_id=observation_id,
                        content_type="application/json",
                    )
                    await sender.send_messages(message)
        finally:
            await credential.close()
    except Exception as exc:
        log.warning(
            "inat.enqueue.send_failed",
            observation_id=observation_id,
            error=str(exc),
            exc_info=True,
        )
        return InatEnqueueResult(success=False, reason="send_failed")

    log.info("inat.enqueue.sent", observation_id=observation_id)
    return InatEnqueueResult(success=True)
