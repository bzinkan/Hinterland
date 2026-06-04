"""Container Apps worker that drains the Service Bus `inat-submit` queue.

Production path under ADR 0010:

    moderation/processor.py or review-queue approve handler
        -> commits `inat_submit_outbox` row in `pending` state
        -> enqueues `{"observation_id": "..."}` to Service Bus `inat-submit`
        -> this worker (KEDA-scaled by queue depth)
        -> `app.inat.submit.submit_observation_to_inat(...)` direct service call
        -> on success: updates `observations.inat_observation_id` +
           `observations.submitted_to_inat_at`, flips the outbox row to
           `submitted`, completes the message.
        -> on transient failure: bumps `retry_count` + `last_error`,
           abandons the message so Service Bus redelivers.
        -> on terminal failure: dead-letters the message and flips the
           outbox row to `dlq` for human intervention.

The worker authenticates to Service Bus via the Container App's
user-assigned managed identity (`DefaultAzureCredential` on Container
Apps falls through to UAMI); the iNat HTTP client uses the bearer
token from `DRAGONFLY_INAT_OAUTH_TOKEN` (Key Vault secret).

Invocation::

    python -m admin.inat_submit_consumer
    python -m admin.inat_submit_consumer --max-messages 50

Per-message contract:

- Parse the Service Bus message body as ``{"observation_id": "..."}``.
- Open a fresh `AsyncSession` for the message.
- Load `observations` + `photos` + `inat_submit_outbox` rows.
- Short-circuit if the observation is already submitted
  (idempotency: a duplicate Service Bus delivery should not double-submit).
- Short-circuit if `moderation_status != "clean"` (race with reject).
- Fetch photo bytes from Blob storage; call
  `submit_observation_to_inat()`; on success update the obs + outbox.
- ``complete_message`` on success or idempotent short-circuit.
- ``abandon_message`` on `InatUnavailable` (transient).
- ``dead_letter_message`` on a row that doesn't exist or that's
  rejected -- those will never succeed on retry.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

import httpx
import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.core.storage import SignedUrlGenerator, _build_generator_for
from app.db import models
from app.inat.client import InatUnavailable, build_inat_client
from app.inat.submit import submit_observation_to_inat

log = structlog.get_logger()


class _MessageParseError(Exception):
    """Body could not be parsed into ``{"observation_id": str}``."""


def _message_body_to_text(message: object) -> str:
    """Decode an Azure Service Bus message body into text."""
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


def parse_inat_submit_payload(body: str) -> str:
    """Extract the observation_id from the Service Bus message body."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise _MessageParseError(f"message body is not JSON: {exc}") from exc
    obs_id = payload.get("observation_id") if isinstance(payload, dict) else None
    if not isinstance(obs_id, str) or not obs_id:
        raise _MessageParseError(f"missing or empty observation_id in payload: {payload!r}")
    return obs_id


async def process_one(
    session: AsyncSession,
    storage: SignedUrlGenerator,
    inat_client: httpx.AsyncClient,
    *,
    body: str,
) -> str:
    """Handle one Service Bus message body.

    Returns ``"complete"`` / ``"abandon"`` / ``"dead_letter"`` so the
    receive loop can pick the right Service Bus disposition.
    """
    try:
        observation_id = parse_inat_submit_payload(body)
    except _MessageParseError as exc:
        log.warning("inat.consumer.parse_failed", body_preview=body[:200], reason=str(exc))
        return "dead_letter"

    obs = (
        await session.execute(
            select(models.Observation).where(models.Observation.id == observation_id)
        )
    ).scalar_one_or_none()

    if obs is None:
        log.warning("inat.consumer.observation_missing", observation_id=observation_id)
        await _flip_outbox_to_dlq(session, observation_id, error="observation_missing")
        return "dead_letter"

    if obs.moderation_status != "clean":
        log.info(
            "inat.consumer.skipped_non_clean",
            observation_id=observation_id,
            moderation_status=obs.moderation_status,
        )
        await _flip_outbox_to_dlq(
            session,
            observation_id,
            error=f"moderation_status={obs.moderation_status}",
        )
        return "dead_letter"

    if obs.inat_observation_id is not None:
        # Idempotency: already submitted. Flip the outbox row to
        # `submitted` if it isn't already and complete the message.
        await _flip_outbox_to_submitted(session, observation_id)
        log.info(
            "inat.consumer.already_submitted_idempotent",
            observation_id=observation_id,
            inat_observation_id=obs.inat_observation_id,
        )
        return "complete"

    photo = (
        await session.execute(select(models.Photo).where(models.Photo.id == obs.photo_id))
    ).scalar_one_or_none()
    if photo is None or photo.status != "clean":
        log.warning(
            "inat.consumer.photo_missing_or_not_clean",
            observation_id=observation_id,
            photo_status=photo.status if photo is not None else None,
        )
        await _flip_outbox_to_dlq(session, observation_id, error="photo_not_clean")
        return "dead_letter"

    try:
        image_bytes = await asyncio.to_thread(
            storage.fetch_object_bytes,
            bucket=photo.bucket,
            object_name=photo.object_name,
        )
    except Exception as exc:
        log.warning(
            "inat.consumer.fetch_bytes_failed",
            observation_id=observation_id,
            error=str(exc),
        )
        await _bump_retry(session, observation_id, error=f"fetch_bytes: {exc}")
        return "abandon"

    try:
        result = await submit_observation_to_inat(
            inat_client,
            dragonfly_observation_id=obs.id,
            photo_bytes=image_bytes,
            latitude=obs.latitude,
            longitude=obs.longitude,
            observed_on=obs.created_at,
            taxon_id=obs.taxon_id,
            species_guess=obs.species_name,
        )
    except InatUnavailable as exc:
        log.warning(
            "inat.consumer.unavailable",
            observation_id=observation_id,
            reason=str(exc),
        )
        await _bump_retry(session, observation_id, error=f"inat_unavailable: {exc}")
        return "abandon"

    obs.inat_observation_id = result.inat_observation_id
    obs.submitted_to_inat_at = datetime.now(UTC)
    await session.execute(
        update(models.InatSubmitOutbox)
        .where(models.InatSubmitOutbox.observation_id == observation_id)
        .values(status="submitted", last_attempt_at=datetime.now(UTC))
    )
    await session.commit()

    log.info(
        "inat.consumer.submitted",
        observation_id=observation_id,
        inat_observation_id=result.inat_observation_id,
    )
    return "complete"


async def _flip_outbox_to_submitted(session: AsyncSession, observation_id: str) -> None:
    await session.execute(
        update(models.InatSubmitOutbox)
        .where(models.InatSubmitOutbox.observation_id == observation_id)
        .values(status="submitted", last_attempt_at=datetime.now(UTC))
    )
    await session.commit()


async def _flip_outbox_to_dlq(session: AsyncSession, observation_id: str, *, error: str) -> None:
    await session.execute(
        update(models.InatSubmitOutbox)
        .where(models.InatSubmitOutbox.observation_id == observation_id)
        .values(status="dlq", last_attempt_at=datetime.now(UTC), last_error=error)
    )
    await session.commit()


async def _bump_retry(session: AsyncSession, observation_id: str, *, error: str) -> None:
    await session.execute(
        update(models.InatSubmitOutbox)
        .where(models.InatSubmitOutbox.observation_id == observation_id)
        .values(
            retry_count=models.InatSubmitOutbox.retry_count + 1,
            last_attempt_at=datetime.now(UTC),
            last_error=error,
        )
    )
    await session.commit()


async def consume(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    storage: SignedUrlGenerator,
    inat_client: httpx.AsyncClient,
    *,
    max_messages: int | None = None,
) -> int:
    """Pull messages from the Service Bus iNat-submit queue until done.

    Returns the number of messages processed. ``max_messages=None`` runs
    until terminated; an integer bounds the loop for smoke tests.
    """
    if not settings.service_bus_enabled:
        log.warning("inat.consumer.not_configured")
        return 0

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
                queue_name=settings.service_bus_inat_queue,
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
                        if max_messages is not None:
                            return processed
                        continue
                    for message in messages:
                        body = _message_body_to_text(message)
                        async with sessions() as session:
                            disposition = await process_one(
                                session, storage, inat_client, body=body
                            )
                        if disposition == "complete":
                            await receiver.complete_message(message)
                        elif disposition == "abandon":
                            await receiver.abandon_message(message)
                        else:
                            await receiver.dead_letter_message(
                                message,
                                reason="inat_consumer_rejected",
                                error_description="rejected by per-message processor",
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
    inat_client = build_inat_client(settings)

    try:
        processed = await consume(
            settings,
            sessions,
            storage,
            inat_client,
            max_messages=args.max_messages,
        )
        log.info("inat.consumer.exit", processed=processed)
    finally:
        await inat_client.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
