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
    web_dist: Path = Path(__file__).resolve().parents[2] / "web" / "dist"

    def __post_init__(self):
        if self.mode not in {"simulation", "hardware"}:
            raise ValueError("BULLSEYE_MODE must be simulation or hardware")
        if self.camera_url and urlparse(self.camera_url).scheme not in {"http", "https"}:
            raise ValueError("CAMERA_STREAM_URL must be an HTTP(S) stream URL")

    @classmethod
    def from_env(cls):
        return cls(
            mode=os.getenv("BULLSEYE_MODE", "simulation"),
            camera_url=os.getenv("CAMERA_STREAM_URL", ""),
            fail_collection_once=os.getenv("BULLSEYE_SIM_FAIL_COLLECTION_ONCE", "0") == "1",
        )
