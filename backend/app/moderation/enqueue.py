"""Relay committed moderation-outbox work to Azure Service Bus."""

from __future__ import annotations

import json
from dataclasses import dataclass

import structlog

from app.core.config import Settings

log = structlog.get_logger()


@dataclass(frozen=True)
class ModerationEnqueueResult:
    success: bool
    reason: str | None = None


async def enqueue_moderation_work(
    *,
    observation_id: str,
    photo_id: str,
    bucket: str,
    object_name: str,
    settings: Settings,
) -> ModerationEnqueueResult:
    """Send a project-owned work envelope; never raise on the API path."""
    if not settings.service_bus_enabled:
        return ModerationEnqueueResult(success=False, reason="not_configured")

    try:
        from azure.identity.aio import DefaultAzureCredential
        from azure.servicebus import ServiceBusMessage
        from azure.servicebus.aio import ServiceBusClient
    except ImportError as exc:  # pragma: no cover - packaging regression
        log.error("moderation.enqueue.import_failed", error=str(exc))
        return ModerationEnqueueResult(success=False, reason="import_failed")

    body = json.dumps(
        {
            "observation_id": observation_id,
            "photo_id": photo_id,
            "bucket": bucket,
            "object_name": object_name,
        },
        separators=(",", ":"),
    )
    credential = DefaultAzureCredential()
    try:
        client = ServiceBusClient(
            fully_qualified_namespace=settings.service_bus_namespace,
            credential=credential,
        )
        async with client:
            sender = client.get_queue_sender(queue_name=settings.service_bus_moderation_queue)
            async with sender:
                await sender.send_messages(
                    ServiceBusMessage(
                        body,
                        message_id=observation_id,
                        content_type="application/json",
                    )
                )
    except Exception as exc:
        log.warning(
            "moderation.enqueue.send_failed",
            observation_id=observation_id,
            error=str(exc),
            exc_info=True,
        )
        return ModerationEnqueueResult(success=False, reason="send_failed")
    finally:
        await credential.close()

    return ModerationEnqueueResult(success=True)
