import asyncio
from contextlib import asynccontextmanager, suppress
import json
from uuid import uuid4

import anyio

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .adapters import HardwareStub, Simulator
from .camera import CameraHub
from .config import Settings
from .mission import CommandError, MissionControl


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    type: str = Field(min_length=1, max_length=40)
    payload: dict = Field(default_factory=dict)


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    camera = CameraHub(settings)
    adapter = Simulator(settings) if settings.mode == "simulation" else HardwareStub()
    control = MissionControl(settings, adapter, camera)

    @asynccontextmanager
    async def lifespan(app):
        await camera.start()
        await control.start()
        yield
        await control.close()
        await camera.close()

    app = FastAPI(title="Bullseye Mission Control", lifespan=lifespan)
    app.state.control, app.state.camera = control, camera

    @app.get("/api/state")
    async def state():
        return control.snapshot()

    @app.post("/api/stop")
    async def stop():
        return await control.stop()

    @app.get("/api/reviews/{review_id}/image")
    async def review_image(review_id: str):
        frame = control.snapshots.get(review_id)
        if not frame:
            raise HTTPException(404, "Review image unavailable")
        return Response(frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.websocket("/ws/control")
    async def control_socket(ws: WebSocket):
        await ws.accept()
        session_id = uuid4().hex
        await ws.send_json({"type": "hello", "session_id": session_id})
        queue = control.subscribe()
        send_lock = asyncio.Lock()

        async def send(message):
            async with send_lock:
                await ws.send_json(message)

        async def updates():
            while True:
                await send(await queue.get())

        sender = asyncio.create_task(updates())
        try:
            while True:
                text = await ws.receive_text()
                if len(text) > 4096:
                    await ws.close(code=1009)
                    break
                request_id = None
                try:
                    data = json.loads(text)
                    request_id = data.get("id") if isinstance(data, dict) else None
                    command = Command.model_validate(data)
                    await control.command(session_id, command.type, command.payload)
                    await send({"type": "ack", "id": command.id, "status": "accepted"})
                except CommandError as error:
                    await send({"type": "ack", "id": request_id, "status": "rejected", "error": str(error)})
                except (ValidationError, ValueError, TypeError):
                    await send({"type": "ack", "id": request_id, "status": "rejected", "error": "Invalid command or payload."})
                except Exception:
                    await control.stop("Adapter command failed. Software stop latched.")
                    await send({"type": "ack", "id": request_id, "status": "rejected",
                                "error": "Adapter command failed. Software stop latched."})
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass
        finally:
            sender.cancel()
            control.unsubscribe(queue)
            # ASGI servers may cancel the connection task on close. Ownership and
            # stop cleanup must still finish before that cancellation escapes.
            with anyio.CancelScope(shield=True):
                await control.disconnect(session_id)
                with suppress(asyncio.CancelledError):
                    await asyncio.gather(sender, return_exceptions=True)

    @app.websocket("/ws/video")
    async def video_socket(ws: WebSocket):
        await ws.accept()
        queue = camera.subscribe()

        async def sender():
            while True:
                frame = await queue.get()
                # A stalled client must never block the shared camera acquisition.
                await asyncio.wait_for(ws.send_bytes(frame), timeout=2)

        async def receiver():
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    return

        tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            pass
        finally:
            for task in tasks:
                task.cancel()
            camera.unsubscribe(queue)
            with suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks, return_exceptions=True)

    if settings.web_dist.is_dir():
        app.mount("/assets", StaticFiles(directory=settings.web_dist / "assets"), name="assets")

    @app.get("/")
    async def index():
        entry = settings.web_dist / "index.html"
        if not entry.is_file():
            raise HTTPException(503, "Build the frontend first: npm run build in mission-control/web")
        return FileResponse(entry, headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
