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
from .bench_demo import BenchControl
from .camera import CameraHub
from .config import Settings
from .mission import CommandError, MissionControl
from .vision import VisionService


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    type: str = Field(min_length=1, max_length=40)
    payload: dict = Field(default_factory=dict)


def create_app(settings: Settings | None = None, *, bench_bridge=None):
    settings = settings or Settings.from_env()
    camera = CameraHub(settings)
    adapter = Simulator(settings) if settings.mode == "simulation" else HardwareStub()
    control = MissionControl(settings, adapter, camera)
    bench = BenchControl(settings, camera, bench_bridge) if settings.bench_enabled else None
    vision = VisionService(settings, camera) if settings.vision_enabled else None
    control.vision = vision
    if bench:
        bench.vision = vision

    @asynccontextmanager
    async def lifespan(app):
        await camera.start()
        if vision:
            await vision.start()
        await control.start()
        if bench:
            await bench.start()
        yield
        if bench:
            await bench.close()
        await control.close()
        if vision:
            await vision.close()
        await camera.close()

    app = FastAPI(title="Bullseye Mission Control", lifespan=lifespan)
    app.state.control, app.state.camera = control, camera
    app.state.bench = bench
    app.state.vision = vision

    @app.get("/api/state")
    async def state():
        return control.snapshot()

    @app.post("/api/stop")
    async def stop():
        if bench:
            await bench.stop()
        return await control.stop()

    @app.get("/api/bench/state")
    async def bench_state():
        if not bench:
            raise HTTPException(404, "Start run-rover-demo.sh to enable the bench demo.")
        return bench.snapshot()

    @app.post("/api/bench/stop")
    async def bench_stop():
        if not bench:
            raise HTTPException(404, "Bench demo is disabled.")
        return await bench.stop()

    @app.get("/api/reviews/{review_id}/image")
    async def review_image(review_id: str):
        frame = control.snapshots.get(review_id)
        if not frame:
            raise HTTPException(404, "Review image unavailable")
        return Response(frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.websocket("/ws/bench")
    @app.websocket("/ws/control")
    async def control_socket(ws: WebSocket):
        await ws.accept()
        controller = bench if ws.url.path == "/ws/bench" else control
        if controller is None:
            await ws.close(code=1008, reason="Bench demo is disabled")
            return
        session_id = uuid4().hex
        await ws.send_json({"type": "hello", "session_id": session_id})
        queue = controller.subscribe()
        seen_ids = set()
        recent_ids = []
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
                    if controller is bench:
                        if command.id in seen_ids:
                            raise CommandError("Duplicate command rejected; movement is never replayed.")
                        seen_ids.add(command.id)
                        recent_ids.append(command.id)
                        if len(recent_ids) > 512:
                            seen_ids.discard(recent_ids.pop(0))
                    if command.type.startswith("vision_"):
                        # Calibration changes affect what the rover sees: pilot only.
                        if vision is None:
                            raise CommandError("Vision is not running on this server.")
                        if controller.pilot != session_id:
                            raise CommandError("Claim pilot controls first.")
                        if bench and bench.operation is not None:
                            raise CommandError("Stop rover motion before changing vision or calibration.")
                        await vision.command(command.type, command.payload)
                        controller.publish()
                    else:
                        await controller.command(session_id, command.type, command.payload)
                    await send({"type": "ack", "id": command.id, "status": "accepted"})
                except CommandError as error:
                    await send({"type": "ack", "id": request_id, "status": "rejected", "error": str(error)})
                except (ValidationError, ValueError, TypeError):
                    await send({"type": "ack", "id": request_id, "status": "rejected", "error": "Invalid command or payload."})
                except Exception:
                    await controller.stop("Adapter command failed. Software stop latched.")
                    await send({"type": "ack", "id": request_id, "status": "rejected",
                                "error": "Adapter command failed. Software stop latched."})
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass
        finally:
            sender.cancel()
            controller.unsubscribe(queue)
            # ASGI servers may cancel the connection task on close. Ownership and
            # stop cleanup must still finish before that cancellation escapes.
            with anyio.CancelScope(shield=True):
                await controller.disconnect(session_id)
                with suppress(asyncio.CancelledError):
                    await asyncio.gather(sender, return_exceptions=True)

    @app.get("/api/vision/state")
    async def vision_state():
        if vision is None:
            raise HTTPException(404, "Vision is disabled (BULLSEYE_VISION=0).")
        return vision.summary()

    @app.get("/api/vision/calibration-frame")
    async def vision_calibration_frame(response: Response):
        response.headers["Cache-Control"] = "no-store"
        if vision is None or not vision.available:
            raise HTTPException(503, "Vision is not available.")
        try:
            return await asyncio.to_thread(vision.calibration_frame)
        except CommandError as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/api/vision/mask.jpg")
    async def vision_mask(kind: str = "target"):
        if vision is None or not vision.available:
            raise HTTPException(503, "Vision is not available.")
        if kind not in ("target", "floor"):
            raise HTTPException(400, "kind must be target or floor")
        frame = await asyncio.to_thread(vision.mask_jpeg, kind)
        if frame is None:
            raise HTTPException(503, "No camera frame yet.")
        return Response(frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.websocket("/ws/vision")
    async def vision_socket(ws: WebSocket):
        await ws.accept()
        if vision is None or not vision.available:
            await ws.close(code=1011, reason="Vision is not available")
            return
        queue = vision.subscribe()

        async def sender():
            while True:
                await asyncio.wait_for(ws.send_json(await queue.get()), timeout=2)

        async def receiver():
            while True:
                if (await ws.receive())["type"] == "websocket.disconnect":
                    return

        tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            pass
        finally:
            for task in tasks:
                task.cancel()
            vision.unsubscribe(queue)
            with suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks, return_exceptions=True)

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

    @app.get("/bench")
    @app.get("/")
    async def index():
        entry = settings.web_dist / "index.html"
        if not entry.is_file():
            raise HTTPException(503, "Build the frontend first: npm run build in mission-control/web")
        return FileResponse(entry, headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
