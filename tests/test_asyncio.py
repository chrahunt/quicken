import asyncio

from unittest.mock import Mock

import pytest

from quicken.lib._asyncio import DeadlineTimer


@pytest.mark.asyncio
async def test_timer_works():
    callback = Mock()
    timer = DeadlineTimer(callback, asyncio.get_running_loop())
    timer.expires_from_now(1)

    await asyncio.sleep(1.1)

    callback.assert_called_once()


@pytest.mark.asyncio
async def test_timer_works_with_zero():
    callback = Mock()
    timer = DeadlineTimer(callback, asyncio.get_running_loop())
    timer.expires_from_now(0)

    await asyncio.sleep(0.01)

    callback.assert_called_once()


@pytest.mark.asyncio
async def test_timer_cancellation():
    callback = Mock()
    timer = DeadlineTimer(callback, asyncio.get_running_loop())
    timer.expires_from_now(1)

    timer.cancel()

    callback.assert_not_called()
