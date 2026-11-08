"""Integration boundary for laptop navigation and the rover communication bridge.

Implement this protocol before advertising hardware capabilities. A drive TTL
MUST also be enforced on the UNO, independently of Python or browser connectivity.
"""
import asyncio
from collections.abc import AsyncIterator
from typing import Protocol

from .config import Settings


class UnsupportedAction(RuntimeError):
    pass


class RoverAdapter(Protocol):
    capabilities: dict[str, bool]
    led_color: str | None
    connected: bool
    distance_cm: float | None

    def search(self, color: str) -> AsyncIterator[str]: ...
    async def collect(self, color: str) -> None: ...
    async def begin_return(self) -> None: ...
    async def drive(self, linear: float, angular: float, ttl_ms: int) -> None: ...
    async def stop(self) -> bool: ...


class Simulator:
    capabilities = {"mission": True, "manual": True, "collection": True, "return": True}
    connected = True
    distance_cm = None

    def __init__(self, settings: Settings):
        self.settings = settings
        self.led_color = None
        self.motion = (0.0, 0.0)
        self.fail_next_collection = settings.fail_collection_once

    async def search(self, color: str):
        await asyncio.sleep(self.settings.search_seconds)
        yield "approaching"
        await asyncio.sleep(self.settings.approach_seconds)
        yield "candidate"

    async def collect(self, color: str):
        await asyncio.sleep(self.settings.collection_seconds)
        if self.fail_next_collection:
            self.fail_next_collection = False
            raise RuntimeError("Simulated LED acknowledgement failed. Please retry collection.")
        self.led_color = "green"

    async def begin_return(self):
        pass

    async def drive(self, linear: float, angular: float, ttl_ms: int):
        self.motion = (linear, angular)

    async def stop(self):
        self.motion = (0.0, 0.0)
        return True


class HardwareStub:
    """Deliberately cannot send movement or claim that hardware has stopped."""

    capabilities = {"mission": False, "manual": False, "collection": False, "return": False}
    led_color = None
    connected = False
    distance_cm = None

    async def search(self, color: str):
        raise UnsupportedAction("Navigation adapter is not connected")
        yield "candidate"

    async def collect(self, color: str):
        raise UnsupportedAction("RGB adapter is not connected")

    async def begin_return(self):
        raise UnsupportedAction("Return adapter is not connected")

    async def drive(self, linear: float, angular: float, ttl_ms: int):
        raise UnsupportedAction("Motor adapter is not connected")

    async def stop(self):
        return False
