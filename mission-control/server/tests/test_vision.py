"""Vision service inside Mission Control: processing, calibration commands and API."""
import asyncio
import json
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from bullseye import vision as vision_module
from bullseye.app import create_app
from bullseye.camera import CameraHub
from bullseye.config import Settings
from bullseye.mission import CommandError
from bullseye.vision import PACKAGE_DIR, VisionService


@pytest.fixture
def vision_dir(tmp_path):
    """Isolated calibration folder: a copy of the repo's vision config, no color model."""
    cfg = json.loads((PACKAGE_DIR / "config.json").read_text())
    cfg["camera"]["controls"] = {}                     # start from a blank calibration, not the arena's
    cfg["rover_mask"] = {"boxes": []}
    cfg["classifier"]["mode"] = "auto"
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    return tmp_path


def arena(block_bottom=450, block_x=400, size=40, color=(30, 30, 220)):
    """White floor, cardboard wall at the top, one painted block."""
    img = np.full((600, 800, 3), 235, np.uint8)
    img[:200] = (90, 136, 176)
    cv2.rectangle(img, (block_x - size // 2, block_bottom - size), (block_x + size // 2, block_bottom), color, -1)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()


def service(vision_dir, mode="simulation"):
    settings = Settings(mode=mode, vision_enabled=True, vision_dir=vision_dir)
    svc = VisionService(settings, CameraHub(settings))
    assert svc.available, svc.error
    return svc


def run(svc, kind, payload=None):
    asyncio.run(svc.command(kind, payload or {}))


def test_processes_simulated_rocks(vision_dir):
    svc = service(vision_dir)
    for color in ("red", "green", "blue", "yellow"):
        svc.set_target(color)
        result = svc._process(CameraHub(Settings()).render_scene(), 1)
        assert result["detection"]["detected"], color
        assert result["box"] and all(0 <= v <= 1 for v in result["box"])
        assert result["nav"]["state"] and result["dry_run"] is True
        json.dumps(result)


def test_result_on_arena_frame(vision_dir):
    svc = service(vision_dir)
    result = svc._process(arena(block_x=600), 7)
    assert result["seq"] == 7 and result["mode"] == "hsv"
    det = result["detection"]
    assert det["detected"] and det["direction"] == "RIGHT"
    assert len(result["free"]["heights"]) == 5
    assert result["nav"]["label"]
    summary = svc.summary()
    assert summary["available"] and summary["detected"] and summary["direction"] == "RIGHT"
    json.dumps(summary)


def test_sampling_fit_and_classifier_mode(vision_dir):
    svc = service(vision_dir)
    svc._process(arena(), 1)
    with pytest.raises(CommandError):
        run(svc, "vision_fit")                                        # no samples yet
    run(svc, "vision_sample", {"cls": "red", "box": [0.48, 0.70, 0.52, 0.74]})
    run(svc, "vision_sample", {"cls": "floor", "box": [0.1, 0.85, 0.4, 0.98]})
    run(svc, "vision_sample", {"cls": "cardboard", "box": [0.1, 0.05, 0.4, 0.25]})
    assert svc.summary()["sample_counts"]["red"] > 0
    run(svc, "vision_undo")
    assert "cardboard" not in svc.summary()["sample_counts"]
    run(svc, "vision_sample", {"cls": "cardboard", "box": [0.1, 0.05, 0.4, 0.25]})
    run(svc, "vision_fit")
    assert (vision_dir / "color_model.json").exists()
    assert (vision_dir / "captures" / "samples.npz").exists()
    summary = svc.summary()
    assert summary["mode"] == "classifier" and summary["confusion"]["red"]["red"] > 0.9
    assert svc._process(arena(), 2)["detection"]["detected"]


def test_sample_validation(vision_dir):
    svc = service(vision_dir)
    with pytest.raises(CommandError):
        run(svc, "vision_sample", {"cls": "red", "box": [0, 0, 1, 1]})   # no frame yet
    svc._process(arena(), 1)
    for bad in ({"cls": "purple", "box": [0, 0, 1, 1]}, {"cls": "red", "box": [0, 0]},
                {"cls": "red", "box": [0.5, 0.5, 0.5, 0.5]}):
        with pytest.raises(CommandError):
            run(svc, "vision_sample", bad)


def test_ground_calibration_from_captures(vision_dir):
    svc = service(vision_dir)
    with pytest.raises(CommandError):
        run(svc, "vision_ground_fit")
    for dist, bottom, size in ((10, 580, 90), (15, 500, 60), (20, 460, 45), (30, 420, 30)):
        svc._process(arena(block_bottom=bottom, size=size), dist)
        run(svc, "vision_ground_capture", {"distance_cm": dist})
    run(svc, "vision_ground_fit")
    ground = svc.summary()["ground"]
    assert ground["calibrated"] and 0.75 < ground["stop_line_y"] < 0.8      # 20 cm -> row 460/600
    saved = json.loads((vision_dir / "config.json").read_text())
    assert saved["ground"]["stop_line_y"] == pytest.approx(ground["stop_line_y"])
    result = svc._process(arena(block_bottom=460, size=45), 99)
    assert result["detection"]["distance_cm_est"] == pytest.approx(20, abs=1.5)
    assert result["lines"]["stop"] == pytest.approx(ground["stop_line_y"])
    run(svc, "vision_tune", {"key": "navigation.arrive_distance_cm", "value": 15})   # moves the stop line
    assert svc.summary()["ground"]["stop_line_y"] > ground["stop_line_y"]


def test_ground_capture_needs_detection(vision_dir):
    svc = service(vision_dir)
    svc._process(arena(color=(235, 235, 235)), 1)          # block invisible on the floor
    with pytest.raises(CommandError):
        run(svc, "vision_ground_capture", {"distance_cm": 20})


def test_fov_steps_tune_mode_snapshot(vision_dir):
    svc = service(vision_dir)
    svc._process(arena(block_x=400), 1)
    run(svc, "vision_fov_capture", {"role": "center"})
    svc._process(arena(block_x=600), 2)                     # error +0.5
    run(svc, "vision_fov_capture", {"role": "side"})
    run(svc, "vision_fov_fit", {"distance_cm": 30, "offset_cm": 10})
    assert svc.summary()["fov"]["hfov_deg"] == pytest.approx(73.7, abs=1.5)

    run(svc, "vision_steps", {"forward": {"200": 4, "500": 11.5, "1000": ""}, "turn": {"200": 15}})
    assert svc.summary()["steps"] == {"forward": {"200": 4.0, "500": 11.5}, "turn": {"200": 15.0}}
    for bad in ({"forward": {"300": 4}}, {"forward": {"200": -1}}, {"turn": "x"}):
        with pytest.raises(CommandError):
            run(svc, "vision_steps", bad)

    run(svc, "vision_tune", {"key": "floor.s_max", "value": 40})
    assert svc.summary()["tuning"]["floor.s_max"] == 40
    for bad in ({"key": "camera.stream_url", "value": 1}, {"key": "floor.s_max", "value": 999},
                {"key": "floor.s_max", "value": True}):
        with pytest.raises(CommandError):
            run(svc, "vision_tune", bad)

    with pytest.raises(CommandError):
        run(svc, "vision_mode", {"mode": "classifier"})      # no model yet
    run(svc, "vision_mode", {"mode": "hsv"})
    run(svc, "vision_snapshot")
    assert list((vision_dir / "captures").glob("*_mc_raw.jpg"))
    with pytest.raises(CommandError):
        run(svc, "vision_launch_rockets")


def test_mask_preview(vision_dir):
    svc = service(vision_dir)
    assert svc.mask_jpeg("target") is None
    svc._process(arena(), 1)
    for kind in ("target", "floor"):
        jpeg = svc.mask_jpeg(kind)
        assert jpeg[:2] == b"\xff\xd8"


def test_unavailable_vision_does_not_break_server(vision_dir, monkeypatch):
    def broken():
        raise ImportError("no cv2")
    monkeypatch.setattr(vision_module, "_import_vision", broken)
    svc = VisionService(Settings(vision_enabled=True, vision_dir=vision_dir), CameraHub(Settings()))
    assert not svc.available and "no cv2" in svc.summary()["error"]
    with pytest.raises(CommandError):
        run(svc, "vision_fit")


def test_api_feed_commands_and_state(vision_dir):
    app = create_app(Settings(mode="simulation", vision_enabled=True, vision_dir=vision_dir))
    with TestClient(app) as client:
        with client.websocket_connect("/ws/vision") as ws:
            frame = ws.receive_json()
        assert frame["type"] == "vision" and frame["detection"]["target_color"] == "red"
        state = client.get("/api/state").json()
        assert state["vision"]["available"]
        assert client.get("/api/vision/state").json()["available"]
        mask = client.get("/api/vision/mask.jpg?kind=floor")
        assert mask.status_code == 200 and mask.headers["content-type"] == "image/jpeg"
        assert client.get("/api/vision/mask.jpg?kind=nope").status_code == 400

        with client.websocket_connect("/ws/control") as ws:
            ws.receive_json()                                            # hello

            def ack(kind, payload=None, rid="x"):
                ws.send_text(json.dumps({"id": rid, "type": kind, "payload": payload or {}}))
                while True:
                    message = ws.receive_json()
                    if message["type"] == "ack" and message["id"] == rid:
                        return message

            assert ack("vision_target", {"color": "blue"}, "a")["status"] == "rejected"   # not pilot
            assert ack("claim", rid="b")["status"] == "accepted"
            assert ack("vision_target", {"color": "blue"}, "c")["status"] == "accepted"
            assert app.state.vision.target == "blue"
            assert ack("select_target", {"color": "yellow"}, "d")["status"] == "accepted"
            assert app.state.vision.target == "yellow"                    # mission target drives vision
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and not app.state.vision.summary()["detected"]:
                time.sleep(0.05)
            assert app.state.vision.summary()["detected"]                 # the simulator's yellow rock


def test_vision_disabled_by_default():
    app = create_app(Settings())
    assert app.state.vision is None
    with TestClient(app) as client:
        assert client.get("/api/state").json()["vision"] is None
        assert client.get("/api/vision/state").status_code == 404


def test_rover_mask_commands_and_frame(vision_dir):
    svc = service(vision_dir)
    run(svc, "vision_mask_add", {"box": [0.05, 0.8, 0.35, 1.0]})
    run(svc, "vision_mask_add", {"box": [0.95, 1.0, 0.6, 0.8]})          # corners in any order
    assert svc.summary()["rover_mask"]["boxes"] == [[0.05, 0.8, 0.35, 1.0], [0.6, 0.8, 0.95, 1.0]]
    assert svc._process(arena(), 1)["mask_boxes"] == svc.summary()["rover_mask"]["boxes"]
    run(svc, "vision_mask_undo")
    assert len(svc.summary()["rover_mask"]["boxes"]) == 1
    run(svc, "vision_mask_clear")
    assert svc.summary()["rover_mask"]["boxes"] == []
    for bad in ({"box": [0, 0, 0.001, 0.001]}, {"box": "x"}):
        with pytest.raises(CommandError):
            run(svc, "vision_mask_add", bad)


def test_camera_settings_validated_saved_and_sent(vision_dir, monkeypatch):
    sent = []

    class Reply:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(vision_module.httpx, "get", lambda url, params, timeout: sent.append((url, params)) or Reply())
    svc = VisionService(Settings(vision_enabled=True, vision_dir=vision_dir,
                                 camera_url="http://192.168.4.1:81/stream"), CameraHub(Settings()))
    run(svc, "vision_camera", {"key": "saturation", "value": 2})
    assert sent[-1] == ("http://192.168.4.1/control", {"var": "saturation", "val": 2})
    assert svc.summary()["camera"]["ok"] is True
    assert json.loads((vision_dir / "config.json").read_text())["camera"]["controls"] == {"saturation": 2}
    for bad in ({"key": "saturation", "value": 3}, {"key": "framesize", "value": 1}, {"key": "ae_level", "value": 0.5}):
        with pytest.raises(CommandError):
            run(svc, "vision_camera", bad)
    sim = service(vision_dir)                                               # no real camera: saved, not sent
    run(sim, "vision_camera", {"key": "ae_level", "value": -1})
    assert "not sent" in sim.summary()["camera"]["message"]


def test_hsv_range_command(vision_dir):
    svc = service(vision_dir)
    run(svc, "vision_hsv", {"color": "red", "h": [176, 10], "s": [100, 255], "v": [70, 255]})
    assert svc.summary()["hsv"]["red"] == {"h": [176, 10], "s": [100, 255], "v": [70, 255]}
    assert json.loads((vision_dir / "config.json").read_text())["colors"]["red"]["h"] == [176, 10]
    for bad in ({"color": "red", "h": [0, 200], "s": [0, 255], "v": [0, 255]},
                {"color": "red", "h": [0, 10], "s": [200, 100], "v": [0, 255]},
                {"color": "purple", "h": [0, 10], "s": [0, 255], "v": [0, 255]}):
        with pytest.raises(CommandError):
            run(svc, "vision_hsv", bad)


def test_teach_hsv_from_a_box(vision_dir):
    svc = service(vision_dir)
    pale_green = (95, 140, 85)                                    # pale green: saturation ~100, below the range
    svc._process(arena(block_bottom=450, block_x=400, size=80, color=pale_green), 1)
    run(svc, "vision_hsv", {"color": "green", "h": [50, 85], "s": [150, 255], "v": [60, 255]})
    svc.set_target("green")
    assert not svc._process(arena(block_bottom=450, block_x=400, size=80, color=pale_green), 2)["detection"]["detected"]
    run(svc, "vision_hsv_teach", {"color": "green", "box": [0.47, 0.68, 0.53, 0.74]})   # inside the block
    taught = svc.summary()["hsv"]["green"]
    assert taught["s"][0] < 150 and svc.summary()["hsv_undo"] == 1
    assert svc._process(arena(block_bottom=450, block_x=400, size=80, color=pale_green), 3)["detection"]["detected"]
    assert "rest of the view" in svc.message
    run(svc, "vision_hsv_undo")
    assert svc.summary()["hsv"]["green"] == {"h": [50, 85], "s": [150, 255], "v": [60, 255]}
    with pytest.raises(CommandError):                             # a box on plain floor teaches nothing
        run(svc, "vision_hsv_teach", {"color": "green", "box": [0.05, 0.85, 0.15, 0.95]})


def test_set_black_exactly_drops_lighter_shadows(vision_dir):
    svc = service(vision_dir)
    img = np.full((600, 800, 3), 235, np.uint8)
    img[:200] = (90, 136, 176)
    cv2.rectangle(img, (100, 380), (260, 520), (45, 45, 45), -1)       # shadow: dark gray
    cv2.rectangle(img, (500, 380), (660, 520), (12, 12, 12), -1)       # the flag: much blacker
    jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()
    svc._process(jpeg, 1)
    run(svc, "vision_hsv_teach", {"color": "black", "box": [0.64, 0.66, 0.81, 0.85], "exact": True})
    black = svc.summary()["hsv"]["black"]
    assert black["v"][1] < 40 and "Set black" in svc.message
    mask = svc.detector.mask(svc._image, "black")
    assert mask[450, 580] > 0 and mask[450, 180] == 0                    # flag yes, shadow no
