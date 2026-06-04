"""Container Apps worker that drains the Service Bus `moderation-pending` queue.

Production path under ADR 0010:

    Blob `photos/pending/<id>.jpg` finalize
        -> Event Grid system topic
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

- Parse the Service Bus message body as a CloudEvent JSON. Extract
  `(bucket, object_name)` from the `subject` field
  (`/blobServices/default/containers/<bucket>/blobs/<object_name>`)
  with a fallback to `data.url` parsing.
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

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.core.storage import SignedUrlGenerator, _build_generator_for
from app.moderation.processor import PhotoNotFound, process_pending_photo
from app.moderation.provider import ModerationUnavailable, Moderator, build_moderator

log = structlog.get_logger()


@dataclass(frozen=True)
class _ParsedBlobLocation:
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


def parse_blob_created_payload(body: str) -> _ParsedBlobLocation:
    """Extract ``(bucket, object_name)`` from an Event Grid BlobCreated CloudEvent.

    Tries `subject` first (canonical
    ``/blobServices/default/containers/<bucket>/blobs/<object>``); falls
    back to parsing `data.url` if subject parsing fails. Raises
    ``_MessageParseError`` if neither yields a valid location.
    """
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise _MessageParseError(f"message body is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise _MessageParseError(f"message body is not a JSON object: {payload!r}")

    subject = payload.get("subject", "")
    if "/blobs/" in subject and "/containers/" in subject:
        container_path, object_name = subject.split("/blobs/", 1)
        bucket = container_path.split("/containers/", 1)[1]
        if bucket and object_name:
            return _ParsedBlobLocation(bucket=bucket, object_name=object_name)

    url = payload.get("data", {}).get("url", "") if isinstance(payload.get("data"), dict) else ""
    marker = ".blob.core.windows.net/"
    if marker in url:
        path = url.split(marker, 1)[1]
        if "/" in path:
            bucket, _, object_name = path.partition("/")
            if bucket and object_name:
                return _ParsedBlobLocation(bucket=bucket, object_name=object_name)

    raise _MessageParseError(f"cannot extract (bucket, object_name) from payload: {payload!r}")


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
        location = parse_blob_created_payload(body)
    except _MessageParseError as exc:
        log.warning("moderation.consumer.parse_failed", body_preview=body[:200], reason=str(exc))
        return "dead_letter"

    try:
        result = await process_pending_photo(
            session,
            storage,
            moderator,
            bucket=location.bucket,
            object_name=location.object_name,
            settings=settings,
        )
    except PhotoNotFound:
        # Race with presign commit -- the producer side will retry by
        # re-firing the BlobCreated event when the upload completes.
        # Abandon for now so Service Bus delivers it again after the
        # lock expires.
        log.info(
            "moderation.consumer.photo_not_found_retry",
            bucket=location.bucket,
            object_name=location.object_name,
        )
        return "abandon"
    except ModerationUnavailable as exc:
        log.warning(
            "moderation.consumer.provider_unavailable",
            bucket=location.bucket,
            object_name=location.object_name,
            reason=str(exc),
        )
        return "abandon"

    log.info(
        "moderation.consumer.processed",
        bucket=location.bucket,
        object_name=location.object_name,
        decision=result.decision,
        observation_id=result.observation_id,
        outbox_status=result.outbox_status,
    )
    return "complete"


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
