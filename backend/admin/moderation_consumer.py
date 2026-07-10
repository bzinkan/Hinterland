"""Drain committed observation-finalization work from the moderation queue.

Production path under ADR 0010:

    observation + photo + moderation-outbox transaction commits
        -> Service Bus queue `moderation-pending`
        -> this worker (KEDA-scaled by queue depth)
        -> `app.moderation.processor.process_pending_photo(...)` direct
           service call under managed identity.

The worker authenticates to Service Bus and to Blob Storage via the
Container App's user-assigned managed identity (`DefaultAzureCredential`
on Container Apps falls through to UAMI). No HTTP hop into the API
process; no /internal/* round trip; the producer side that wrote the
outbox row is the only piece this worker shares state with.

Invocation::

    python -m admin.moderation_consumer
    python -m admin.moderation_consumer --max-messages 50   # tests / smoke

Per-message contract:

- Parse a project-owned committed-work envelope containing observation,
  photo, bucket, and object identifiers. BlobCreated CloudEvents are rejected.
- Open a fresh `AsyncSession` for the message; call
  `process_pending_photo(...)`; on clean the processor itself writes
  the `inat_submit_outbox` row + attempts the iNat-submit enqueue.
- ``complete_message`` on success.
- ``abandon_message`` on transient failure (Service Bus retries until
  ``max_delivery_count`` then dead-letters per queue policy).
- ``dead_letter_message`` on a parse failure that will never succeed
  (so the message doesn't ping-pong on every redelivery).

Run-forever vs bounded mode:

- ``--max-messages N`` exits after processing N messages. Used by
  smoke tests. Default is unbounded (production).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.core.storage import SignedUrlGenerator, _build_generator_for
from app.db import models
from app.moderation.processor import (
    ModerationWorkInvalid,
    PhotoNotFound,
    process_pending_photo,
)
from app.moderation.provider import ModerationUnavailable, Moderator, build_moderator

log = structlog.get_logger()


@dataclass(frozen=True)
class _ParsedModerationWork:
    observation_id: str
    photo_id: str
    bucket: str
    object_name: str


class _MessageParseError(Exception):
    """Raised when the Service Bus message body cannot be parsed.

    Caller dead-letters the message rather than retrying -- a malformed
    payload will never succeed on retry.
    """


def _message_body_to_text(message: object) -> str:
    """Decode an Azure Service Bus message body into text.

    Unit tests often pass simple strings, but real Service Bus received
    messages expose body bytes/iterables. Falling back to ``str(message)``
    would parse the SDK object's representation instead of the payload.
    """
    body = getattr(message, "body", None)
    if body is None:
        return str(message)
    if isinstance(body, str):
        return body
    if isinstance(body, bytes):
        return body.decode("utf-8")

    try:
        parts = list(body)
    except TypeError:
        return str(body)

    rendered: list[str] = []
    for part in parts:
        if isinstance(part, bytes):
            rendered.append(part.decode("utf-8"))
        else:
            rendered.append(str(part))
    return "".join(rendered)


def parse_moderation_work_payload(body: str) -> _ParsedModerationWork:
    """Parse work emitted only after observation finalization commits."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise _MessageParseError(f"message body is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise _MessageParseError(f"message body is not a JSON object: {payload!r}")

    if payload.get("type") == "Microsoft.Storage.BlobCreated" or "subject" in payload:
        raise _MessageParseError("BlobCreated events are not valid moderation work")

    observation_id = payload.get("observation_id")
    photo_id = payload.get("photo_id")
    bucket = payload.get("bucket")
    object_name = payload.get("object_name")
    if not all(
        isinstance(value, str) and bool(value)
        for value in (observation_id, photo_id, bucket, object_name)
    ):
        raise _MessageParseError(f"missing committed-work identifiers: {payload!r}")
    assert isinstance(observation_id, str)
    assert isinstance(photo_id, str)
    assert isinstance(bucket, str)
    assert isinstance(object_name, str)
    if not object_name.startswith("pending/finalized/"):
        raise _MessageParseError("moderation work must reference a finalized canonical object")

    return _ParsedModerationWork(
        observation_id=observation_id,
        photo_id=photo_id,
        bucket=bucket,
        object_name=object_name,
    )


async def process_one(
    session: AsyncSession,
    storage: SignedUrlGenerator,
    moderator: Moderator,
    settings: Settings,
    *,
    body: str,
) -> str:
    """Handle one Service Bus message body.

    Returns one of ``"complete"``, ``"abandon"``, ``"dead_letter"`` so the
    receive loop can pick the right Service Bus disposition. This split
    keeps the unit-testable business logic separate from the Service Bus
    SDK loop (which is harder to fake cleanly).
    """
    try:
        work = parse_moderation_work_payload(body)
    except _MessageParseError as exc:
        log.warning("moderation.consumer.parse_failed", body_preview=body[:200], reason=str(exc))
        return "dead_letter"

    outbox = (
        await session.execute(
            select(models.ModerationOutbox)
            .where(models.ModerationOutbox.observation_id == work.observation_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if outbox is None or outbox.photo_id != work.photo_id:
        log.warning(
            "moderation.consumer.outbox_missing_or_mismatched",
            observation_id=work.observation_id,
            photo_id=work.photo_id,
        )
        return "dead_letter"
    if outbox.status == "succeeded":
        return "complete"

    now = datetime.now(UTC)
    if outbox.status == "processing" and outbox.lease_until is not None:
        lease_until = outbox.lease_until
        if lease_until.tzinfo is None:
            lease_until = lease_until.replace(tzinfo=UTC)
        if lease_until > now:
            return "abandon"

    outbox.status = "processing"
    outbox.lease_until = now + timedelta(minutes=10)
    outbox.last_attempt_at = now
    await session.execute(
        update(models.Observation)
        .where(
            models.Observation.id == work.observation_id,
            models.Observation.moderation_status.not_in(
                ("clean", "quarantine", "pilot_private", "rejected")
            ),
        )
        .values(moderation_status="processing")
    )
    await session.commit()

    try:
        result = await process_pending_photo(
            session,
            storage,
            moderator,
            bucket=work.bucket,
            object_name=work.object_name,
            settings=settings,
            expected_photo_id=work.photo_id,
            expected_observation_id=work.observation_id,
        )
    except PhotoNotFound:
        log.info(
            "moderation.consumer.photo_not_found_retry",
            bucket=work.bucket,
            object_name=work.object_name,
        )
        exhausted = await _mark_failed(session, outbox, "photo_not_found")
        return "dead_letter" if exhausted else "abandon"
    except ModerationUnavailable as exc:
        log.warning(
            "moderation.consumer.provider_unavailable",
            bucket=work.bucket,
            object_name=work.object_name,
            reason=str(exc),
        )
        exhausted = await _mark_failed(session, outbox, f"provider_unavailable: {exc}")
        return "dead_letter" if exhausted else "abandon"
    except ModerationWorkInvalid as exc:
        outbox.status = "dlq"
        outbox.lease_until = None
        outbox.last_error = str(exc)
        await session.execute(
            update(models.Observation)
            .where(models.Observation.id == work.observation_id)
            .values(moderation_status="failed")
        )
        await session.commit()
        return "dead_letter"
    except Exception as exc:
        log.warning(
            "moderation.consumer.processing_failed",
            observation_id=work.observation_id,
            error=str(exc),
        )
        exhausted = await _mark_failed(session, outbox, f"processing_failed: {exc}")
        return "dead_letter" if exhausted else "abandon"

    if outbox.status != "succeeded":
        log.error(
            "moderation.consumer.processor_left_outbox_incomplete",
            observation_id=work.observation_id,
            status=outbox.status,
        )
        exhausted = await _mark_failed(
            session,
            outbox,
            "processor_returned_without_atomic_outbox_completion",
        )
        return "dead_letter" if exhausted else "abandon"

    log.info(
        "moderation.consumer.processed",
        bucket=work.bucket,
        object_name=work.object_name,
        decision=result.decision,
        observation_id=result.observation_id,
        outbox_status=result.outbox_status,
    )
    return "complete"


async def _mark_failed(
    session: AsyncSession,
    outbox: models.ModerationOutbox,
    error: str,
) -> bool:
    outbox.retry_count += 1
    exhausted = outbox.retry_count >= 5
    outbox.status = "dlq" if exhausted else "failed"
    outbox.lease_until = None
    outbox.last_attempt_at = datetime.now(UTC)
    outbox.last_error = error
    await session.execute(
        update(models.Observation)
        .where(models.Observation.id == outbox.observation_id)
        .values(moderation_status="failed")
    )
    await session.commit()
    return exhausted


async def consume(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    storage: SignedUrlGenerator,
    moderator: Moderator,
    *,
    max_messages: int | None = None,
) -> int:
    """Pull messages from the Service Bus moderation queue until done.

    Returns the number of messages processed. When ``max_messages`` is
    None (production default) the loop runs until the process is
    terminated. When set, exits after that many messages -- used by
    smoke tests + tooling.
    """
    if not settings.service_bus_enabled:
        log.warning("moderation.consumer.not_configured")
        return 0

    # Lazy imports keep the Service Bus SDK optional at module-load
    # time so the unit tests for ``parse_blob_created_payload`` and
    # ``process_one`` don't need the package installed.
    from azure.identity.aio import DefaultAzureCredential
    from azure.servicebus.aio import ServiceBusClient

    processed = 0
    credential = DefaultAzureCredential()
    try:
        client = ServiceBusClient(
            fully_qualified_namespace=settings.service_bus_namespace,
            credential=credential,
        )
        async with client:
            receiver = client.get_queue_receiver(
                queue_name=settings.service_bus_moderation_queue,
            )
            async with receiver:
                while True:
                    if max_messages is not None and processed >= max_messages:
                        return processed
                    messages = await receiver.receive_messages(
                        max_message_count=settings.service_bus_receive_batch_size,
                        max_wait_time=settings.service_bus_receive_max_wait_seconds,
                    )
                    if not messages:
                        # No work right now. In bounded mode we exit;
                        # in unbounded mode the next iteration polls.
                        if max_messages is not None:
                            return processed
                        continue
                    for message in messages:
                        body = _message_body_to_text(message)
                        async with sessions() as session:
                            disposition = await process_one(
                                session, storage, moderator, settings, body=body
                            )
                        if disposition == "complete":
                            await receiver.complete_message(message)
                        elif disposition == "abandon":
                            await receiver.abandon_message(message)
                        else:
                            await receiver.dead_letter_message(
                                message,
                                reason="parse_failed",
                                error_description="moderation_consumer rejected payload",
                            )
                        processed += 1
                        if max_messages is not None and processed >= max_messages:
                            return processed
    finally:
        await credential.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-messages",
        type=int,
        default=None,
        help="Exit after processing N messages (default: run forever)",
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    settings = get_settings()
    engine = create_async_engine(settings.sqlalchemy_database_url)
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    storage = _build_generator_for(settings)
    moderator = build_moderator(settings)

    try:
        processed = await consume(
            settings,
            sessions,
            storage,
            moderator,
            max_messages=args.max_messages,
        )
        log.info("moderation.consumer.exit", processed=processed)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
