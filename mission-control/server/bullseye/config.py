from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class Settings:
    mode: str = "simulation"
    camera_url: str = ""
    search_seconds: float = 3.0
    approach_seconds: float = 3.0
    lease_seconds: float = 3.0
    drive_seconds: float = 0.3
    collection_seconds: float = 0.8
    fail_collection_once: bool = False
    bench_enabled: bool = False
    rover_host: str = "192.168.4.1"
    rover_port: int = 100
    scooper_url: str = "http://192.168.4.50"  # Scooper ESP32-S3 on the rover's ELEGOO Wi-Fi.
    scooper_transport: str = "ble"
    ble_display_device_id: str | None = None
    web_dist: Path = Path(__file__).resolve().parents[2] / "web" / "dist"
    # Vision runs inside this server on the camera relay's frames (bullseye/vision.py).
    # Off by default for tests; from_env() turns it on unless BULLSEYE_VISION=0.
    vision_enabled: bool = False
    vision_dir: Path = Path(__file__).resolve().parents[3] / "software" / "vision"

    def __post_init__(self):
        if self.mode not in {"simulation", "hardware"}:
            raise ValueError("BULLSEYE_MODE must be simulation or hardware")
        if self.scooper_transport not in {"ble", "http"}:
            raise ValueError("SCOOPER_TRANSPORT must be ble or http")
        if self.camera_url and urlparse(self.camera_url).scheme not in {"http", "https"}:
            raise ValueError("CAMERA_STREAM_URL must be an HTTP(S) stream URL")
        if urlparse(self.scooper_url).scheme not in {"http", "https"} or not urlparse(self.scooper_url).hostname:
            raise ValueError("SCOOPER_URL must be an HTTP URL such as http://192.168.4.50")
        if not 1 <= self.rover_port <= 65535:
            raise ValueError("ROVER_PORT must be 1–65535")
        if self.bench_enabled and self.mode != "hardware":
            raise ValueError("The real-rover bench demo requires BULLSEYE_MODE=hardware")

    @classmethod
    def from_env(cls):
        bench = os.getenv("BULLSEYE_BENCH_ENABLED", "0") == "1"
        return cls(
            mode=os.getenv("BULLSEYE_MODE", "simulation"),
            camera_url=os.getenv("CAMERA_STREAM_URL", "http://192.168.4.1:81/stream" if bench else ""),
            fail_collection_once=os.getenv("BULLSEYE_SIM_FAIL_COLLECTION_ONCE", "0") == "1",
            bench_enabled=bench,
            rover_host=os.getenv("ROVER_HOST", "192.168.4.1"),
            rover_port=int(os.getenv("ROVER_PORT", "100")),
            scooper_url=os.getenv("SCOOPER_URL", "http://192.168.4.50"),
            scooper_transport=os.getenv("SCOOPER_TRANSPORT", "ble"),
            ble_display_device_id=os.getenv("BLE_DISPLAY_DEVICE_ID") or None,
            vision_enabled=os.getenv("BULLSEYE_VISION", "1") == "1",
            vision_dir=Path(os.getenv("BULLSEYE_VISION_DIR", str(cls.vision_dir))),
        )
