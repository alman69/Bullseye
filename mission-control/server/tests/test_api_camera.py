import asyncio
from io import BytesIO
import time

from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest

from bullseye.app import create_app
from bullseye.camera import CameraHub, JpegParser
from bullseye.config import Settings


def command(ws, kind, payload=None):
    command.counter += 1
    request_id = str(command.counter)
    ws.send_json({"id": request_id, "type": kind, "payload": payload or {}})
    while True:
        message = ws.receive_json()
        if message.get("type") == "ack" and message.get("id") == request_id:
            return message


command.counter = 0


def test_websocket_pilots_stop_and_video():
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/control") as one, client.websocket_connect("/ws/control") as two:
            first = one.receive_json()
            second = two.receive_json()
            assert first["session_id"] != second["session_id"]
            assert command(one, "claim")["status"] == "accepted"
            assert command(two, "claim")["status"] == "rejected"
            assert command(two, "start")["status"] == "rejected"
            assert client.post("/api/stop").json()["phase"] == "stopped"
            assert command(one, "reset")["status"] == "accepted"
            assert command(one, "drive", {"linear": .3, "angular": 0})["status"] == "accepted"
            with client.websocket_connect("/ws/video") as video:
                image = video.receive_bytes()
                assert Image.open(BytesIO(image)).size == (800, 450)
        assert client.get("/api/state").json()["phase"] == "stopped"


def test_malformed_commands_are_rejected_without_breaking_connection():
    with TestClient(create_app()) as client:
        with client.websocket_connect("/ws/control") as ws:
            ws.receive_json()
            ws.send_text("not-json")
            while True:
                message = ws.receive_json()
                if message["type"] == "ack":
                    assert message["status"] == "rejected"
                    break
            assert command(ws, "claim")["status"] == "accepted"
        assert client.get("/api/reviews/missing/image").status_code == 404


def test_jpeg_parser_handles_fragmented_multipart_and_size_limits():
    frame = CameraHub(Settings()).render_scene()
    stream = b"--camera\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n--camera\r\n" + frame
    parser = JpegParser()
    frames = []
    for i in range(0, len(stream), 7):
        frames.extend(parser.feed(stream[i:i+7]))
    assert frames == [frame, frame]
    with pytest.raises(ValueError):
        JpegParser(max_bytes=20).feed(b"\xff\xd8" + b"x" * 100)


async def test_camera_fanout_drops_old_frames_and_reports_stale():
    camera = CameraHub(Settings())
    one, two = camera.subscribe(), camera.subscribe()
    camera.publish(b"first")
    camera.publish(b"latest")
    assert one.qsize() == two.qsize() == 1
    assert one.get_nowait() == two.get_nowait() == b"latest"
    camera.frame_at = time.monotonic() - 3
    assert camera.status()["status"] == "stale"


async def test_live_relay_reads_fragmented_http_stream_and_recovers(monkeypatch):
    settings = Settings(camera_url="http://camera.test/stream")
    frame = CameraHub(Settings()).render_scene()
    attempts = 0

    class FixtureStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for offset in range(0, len(frame), 127):
                yield frame[offset:offset+127]
            await asyncio.sleep(.15)
            raise httpx.ReadError("Fixture interrupted")

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, headers={"content-type": "multipart/x-mixed-replace; boundary=fixture"}, stream=FixtureStream())

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    camera = CameraHub(settings)
    await camera.start()
    try:
        async with asyncio.timeout(3):
            while attempts < 2 or camera.sequence < 2:
                await asyncio.sleep(.02)
        assert camera.latest == frame and camera.source == "live"
    finally:
        await camera.close()
