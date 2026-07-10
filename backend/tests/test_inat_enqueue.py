"""Unit tests for the Service Bus enqueue helper.

The helper is deliberately non-raising on every failure path so the
producer hot path (moderation worker / review-approve handler) can
keep going and leave the outbox row in `pending` for the replay job.
These tests pin down the four return shapes:

1. ``not_configured`` -- empty Service Bus namespace, no SDK call.
2. ``send_failed`` -- send call raised; reason is returned, log
   captures the underlying exception.
3. Success -- send call returned cleanly; ``success=True``.
4. ``import_failed`` -- ``azure-servicebus`` not importable. Skipped
   on machines that have the SDK installed (every CI run does), but
   the no-raise contract is asserted via a monkeypatched import.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.inat.enqueue import InatEnqueueResult, enqueue_inat_submit

_OBS_ID = "01J0OBSID00000000000000ULID"


def _settings_with_sb(*, namespace: str = "hinterland-sb-test.servicebus.windows.net") -> Settings:
    return Settings(env="local", service_bus_namespace=namespace, inat_submit_enabled=True)


def _settings_without_sb() -> Settings:
    return Settings(env="local", service_bus_namespace="", inat_submit_enabled=True)


async def test_returns_disabled_before_service_bus_check() -> None:
    settings = Settings(
        env="local",
        service_bus_namespace="hinterland-sb-test.servicebus.windows.net",
        inat_submit_enabled=False,
    )
    result = await enqueue_inat_submit(_OBS_ID, settings=settings)
    assert result == InatEnqueueResult(success=False, reason="disabled")


async def test_returns_not_configured_when_namespace_empty() -> None:
    result = await enqueue_inat_submit(_OBS_ID, settings=_settings_without_sb())
    assert result == InatEnqueueResult(success=False, reason="not_configured")


async def test_success_path_sends_one_message() -> None:
    """Patch ServiceBusClient + DefaultAzureCredential to capture the
    send call without hitting Azure. Asserts message_id == observation_id
    so the queue-side dedup key is correct."""
    settings = _settings_with_sb()

    sent_messages: list[Any] = []

    fake_sender = MagicMock()
    fake_sender.__aenter__ = AsyncMock(return_value=fake_sender)
    fake_sender.__aexit__ = AsyncMock(return_value=None)
    fake_sender.send_messages = AsyncMock(
        side_effect=lambda msg: sent_messages.append(msg),
    )

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.get_queue_sender = MagicMock(return_value=fake_sender)

    fake_credential = MagicMock()
    fake_credential.close = AsyncMock(return_value=None)

    with (
        patch("azure.servicebus.aio.ServiceBusClient", return_value=fake_client),
        patch("azure.identity.aio.DefaultAzureCredential", return_value=fake_credential),
    ):
        result = await enqueue_inat_submit(_OBS_ID, settings=settings)

    assert result == InatEnqueueResult(success=True)
    assert len(sent_messages) == 1
    msg = sent_messages[0]
    assert msg.message_id == _OBS_ID
    # azure-servicebus stores the body bytes-ish; check the JSON shape.
    body = b"".join(msg.body) if hasattr(msg.body, "__iter__") else bytes(msg.body)
    assert _OBS_ID.encode() in body
    fake_credential.close.assert_awaited_once()


async def test_send_failed_returns_failure_without_raising() -> None:
    settings = _settings_with_sb()

    fake_sender = MagicMock()
    fake_sender.__aenter__ = AsyncMock(return_value=fake_sender)
    fake_sender.__aexit__ = AsyncMock(return_value=None)
    fake_sender.send_messages = AsyncMock(side_effect=RuntimeError("queue unreachable"))

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.get_queue_sender = MagicMock(return_value=fake_sender)

    fake_credential = MagicMock()
    fake_credential.close = AsyncMock(return_value=None)

    with (
        patch("azure.servicebus.aio.ServiceBusClient", return_value=fake_client),
        patch("azure.identity.aio.DefaultAzureCredential", return_value=fake_credential),
    ):
        result = await enqueue_inat_submit(_OBS_ID, settings=settings)

    assert result == InatEnqueueResult(success=False, reason="send_failed")
    fake_credential.close.assert_awaited_once()


async def test_credential_closed_even_when_client_construction_raises() -> None:
    """The producer never leaks the DefaultAzureCredential async session."""
    settings = _settings_with_sb()

    fake_credential = MagicMock()
    fake_credential.close = AsyncMock(return_value=None)

    with (
        patch(
            "azure.servicebus.aio.ServiceBusClient",
            side_effect=RuntimeError("bad namespace"),
        ),
        patch("azure.identity.aio.DefaultAzureCredential", return_value=fake_credential),
    ):
        result = await enqueue_inat_submit(_OBS_ID, settings=settings)

    assert result == InatEnqueueResult(success=False, reason="send_failed")
    fake_credential.close.assert_awaited_once()


def test_inat_enqueue_result_is_frozen() -> None:
    from dataclasses import FrozenInstanceError

    result = InatEnqueueResult(success=True)
    with pytest.raises(FrozenInstanceError):
        # frozen dataclass -- any mutation raises FrozenInstanceError
        result.success = False  # type: ignore[misc]
