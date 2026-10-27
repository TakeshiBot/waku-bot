"""Turn-local media deliveries that run after the final AI text is published."""

from collections.abc import Awaitable, Callable
from typing import Any


class DeferredDeliveryError(RuntimeError):
    """Photos failed after the AI reply; this is distinct from model failure."""


class DeferredDelivery:
    def __init__(self):
        self._pending: list[
            tuple[Callable[[], Awaitable[Any]], Callable[[], Awaitable[None]]]
        ] = []

    def add(self, send, cleanup):
        self._pending.append((send, cleanup))

    async def flush(self):
        while self._pending:
            send, cleanup = self._pending.pop(0)
            try:
                result = await send()
                if not result.success:
                    raise DeferredDeliveryError(result.message or "Deferred image delivery failed")
            except DeferredDeliveryError:
                raise
            except Exception as error:
                raise DeferredDeliveryError(
                    f"Photo delivery failed ({type(error).__name__}): {error}"
                ) from error
            finally:
                await cleanup()

    async def discard(self):
        # Release image reservations even if generation or text delivery failed.
        while self._pending:
            _, cleanup = self._pending.pop(0)
            await cleanup()
