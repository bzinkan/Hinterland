from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.config import Settings
from app.moderation.enqueue import ModerationEnqueueResult, enqueue_moderation_work


def _kwargs(settings: Settings) -> dict[str, Any]:
    return {
        "observation_id": "01J0OBSID00000000000000ULID",
        "photo_id": "01J0PHOTOID00000000000ULID",
        "bucket": "photos",
        "object_name": "pending/finalized/01J0PHOTOID00000000000ULID.jpg",
        "settings": settings,
    }


async def test_enqueue_not_configured_is_non_raising() -> None:
    result = await enqueue_moderation_work(**_kwargs(Settings(service_bus_namespace="")))
    assert result == ModerationEnqueueResult(success=False, reason="not_configured")


async def test_enqueue_sends_committed_work_envelope() -> None:
    settings = Settings(service_bus_namespace="test.servicebus.windows.net")
    sent: list[Any] = []
    sender = MagicMock()
    sender.__aenter__ = AsyncMock(return_value=sender)
    sender.__aexit__ = AsyncMock(return_value=None)
    sender.send_messages = AsyncMock(side_effect=lambda message: sent.append(message))
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get_queue_sender = MagicMock(return_value=sender)
    credential = MagicMock()
    credential.close = AsyncMock()

    with (
        patch("azure.servicebus.aio.ServiceBusClient", return_value=client),
        patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
    ):
        result = await enqueue_moderation_work(**_kwargs(settings))

    assert result.success is True
    assert len(sent) == 1
    body = b"".join(sent[0].body).decode("utf-8")
    assert '"observation_id":"01J0OBSID00000000000000ULID"' in body
    assert '"photo_id":"01J0PHOTOID00000000000ULID"' in body
    credential.close.assert_awaited_once()
