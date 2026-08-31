"""Ctrl+C handling mapped to a CancellationToken."""

import asyncio
import signal


class CancelState:
    """CancellationToken implementation driven by SIGINT."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()

    def cancel(self) -> None:
        self._event.set()

    def reset(self) -> None:
        self._event.clear()

    def install(self) -> None:
        """Route SIGINT to this token inside the running loop."""

        def handler(signum: int, frame: object) -> None:  # noqa: ARG001
            self.cancel()

        try:
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGINT, self.cancel)
        except (NotImplementedError, RuntimeError):
            signal.signal(signal.SIGINT, handler)
