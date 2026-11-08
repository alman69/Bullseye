"""Learned per-pixel color classifier plus floor-referenced white balance.

Why: fixed HSV ranges failed on the real arena. Far mineral cubes are pale on
this camera (S 30-75), yellow overlaps cardboard, and orange paper sits next to
red. Instead of one range per color, every pixel is assigned to the CLOSEST of
the arena's real materials (red/green/blue/yellow blocks, white floor,
cardboard, obstacle paper), learned from drag-box samples. Because cardboard and
paper are their own classes, they compete with the minerals instead of leaking
into them.

Model: one Gaussian per class in CIE Lab (mean + regularized 3x3 covariance).
A pixel gets the class with the smallest Mahalanobis distance, or UNKNOWN (255)
if even that distance exceeds `max_distance`. At runtime a precomputed
32x32x32 BGR lookup table makes classification a single array lookup.

White balance: the matte white floor is a free reference in every frame. The
median color of likely-floor pixels near the bottom is mapped back to the floor
color recorded at calibration time, which cancels the camera's auto white
balance drift and a floodlight's color temperature.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

UNKNOWN = 255
SAMPLE_CLASSES = ("red", "green", "blue", "yellow", "floor", "cardboard", "obstacle")
BITS = 5                                   # lookup-table resolution per BGR channel
SHIFT = 8 - BITS


def bgr_to_lab(pixels: np.ndarray) -> np.ndarray:
    """(N,3) uint8 BGR -> (N,3) float32 Lab (OpenCV 8-bit scale)."""
    px = np.ascontiguousarray(pixels, dtype=np.uint8).reshape(-1, 1, 3)
    return cv2.cvtColor(px, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)


class WhiteBalance:
    """Per-channel gains that map today's floor color to the calibration-time floor color."""

    def __init__(self, reference_bgr=None, alpha: float = 0.2, min_floor: float = 0.02,
                 gain_min: float = 0.6, gain_max: float = 1.6):
        self.reference = None if reference_bgr is None else np.asarray(reference_bgr, np.float32)
        self.alpha, self.min_floor = alpha, min_floor
        self.gain_min, self.gain_max = gain_min, gain_max
        self.gains = np.ones(3, np.float32)
        self._initialized = False

    def update(self, frame_bgr: np.ndarray) -> np.ndarray:
        if self.reference is None:
            return self.gains
        h = frame_bgr.shape[0]
        roi = frame_bgr[int(h * 0.7):, :][::4, ::4]               # bottom 30%, subsampled
        hsv = cv2.cvtColor(np.ascontiguousarray(roi), cv2.COLOR_BGR2HSV)
        bright = hsv[..., 2] > 100
        if bright.mean() < self.min_floor:
            return self.gains                                     # not enough floor: keep previous gains
        # The white floor is the least saturated bright thing near the bottom; a color cast
        # raises its saturation, so pick the least-saturated 40% instead of a fixed cutoff.
        sat = hsv[..., 1][bright]
        floorish = bright & (hsv[..., 1] <= np.percentile(sat, 40))
        if np.median(hsv[..., 1][floorish]) > 100:
            return self.gains                                     # nothing floor-like: keep previous gains
        median = np.median(roi[floorish].astype(np.float32), axis=0)
        target = np.clip(self.reference / np.maximum(median, 1.0), self.gain_min, self.gain_max)
        if not self._initialized:
            self.gains, self._initialized = target.astype(np.float32), True
        else:
            self.gains = ((1 - self.alpha) * self.gains + self.alpha * target).astype(np.float32)
        return self.gains

    def apply(self, frame_bgr: np.ndarray) -> np.ndarray:
        if np.allclose(self.gains, 1.0, atol=1e-3):
            return frame_bgr
        ramp = np.arange(256, dtype=np.float32)
        table = np.stack([np.clip(ramp * g, 0, 255) for g in self.gains], axis=-1).astype(np.uint8)
        return cv2.LUT(frame_bgr, table.reshape(256, 1, 3))


class ColorModel:
    def __init__(self, classes, means, covs, max_distance=4.0, reference_floor=None, counts=None):
        self.classes = list(classes)
        self.means = np.asarray(means, np.float32).reshape(-1, 3)
        self.covs = np.asarray(covs, np.float32).reshape(-1, 3, 3)
        self.inv_covs = np.linalg.inv(self.covs).astype(np.float32)
        self.max_distance = float(max_distance)
        self.reference_floor = None if reference_floor is None else [float(v) for v in reference_floor]
        self.counts = dict(counts or {})
        self.lut = self._build_lut()

    # -- fitting / persistence ---------------------------------------------
    @classmethod
    def fit(cls, samples: dict, max_distance: float = 4.0, regularization: float = 4.0,
            floor_class: str = "floor", min_pixels: int = 30) -> "ColorModel":
        classes, means, covs, counts = [], [], [], {}
        for name in SAMPLE_CLASSES + tuple(k for k in samples if k not in SAMPLE_CLASSES):
            px = samples.get(name)
            if px is None or len(px) < min_pixels:
                continue
            lab = bgr_to_lab(px)
            classes.append(name)
            means.append(lab.mean(axis=0))
            covs.append(np.cov(lab.T) + regularization * np.eye(3))
            counts[name] = int(len(px))
        if len(classes) < 2:
            raise ValueError("sample at least two classes (for example a mineral and the floor)")
        floor = samples.get(floor_class)
        reference = np.median(floor.astype(np.float32), axis=0) if floor is not None and len(floor) >= min_pixels else None
        return cls(classes, means, covs, max_distance, reference, counts)

    def to_dict(self) -> dict:
        return {"classes": self.classes, "means": self.means.round(3).tolist(),
                "covs": self.covs.round(4).tolist(), "max_distance": self.max_distance,
                "reference_floor_bgr": self.reference_floor, "sample_counts": self.counts}

    @classmethod
    def from_dict(cls, d: dict) -> "ColorModel":
        return cls(d["classes"], d["means"], d["covs"], d.get("max_distance", 4.0),
                   d.get("reference_floor_bgr"), d.get("sample_counts"))

    def save(self, path) -> Path:
        path = Path(path)
        path.write_text(json.dumps(self.to_dict(), indent=1) + "\n", encoding="utf-8")
        return path

    @classmethod
    def load(cls, path) -> "ColorModel":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    # -- classification -----------------------------------------------------
    def classify_lab(self, lab: np.ndarray) -> np.ndarray:
        """(N,3) Lab -> (N,) class index or UNKNOWN."""
        best = np.full(len(lab), np.inf, np.float32)
        labels = np.full(len(lab), UNKNOWN, np.uint8)
        for i in range(len(self.classes)):
            diff = lab - self.means[i]
            d2 = np.einsum("nj,jk,nk->n", diff, self.inv_covs[i], diff)
            better = d2 < best
            best[better] = d2[better]
            labels[better] = i
        labels[best > self.max_distance ** 2] = UNKNOWN
        return labels

    def classify_bgr(self, pixels: np.ndarray) -> np.ndarray:
        return self.classify_lab(bgr_to_lab(pixels))

    def _build_lut(self) -> np.ndarray:
        centers = (np.arange(2 ** BITS, dtype=np.uint16) << SHIFT) + (1 << (SHIFT - 1))
        b, g, r = np.meshgrid(centers, centers, centers, indexing="ij")
        grid = np.stack([b.ravel(), g.ravel(), r.ravel()], axis=1).astype(np.uint8)
        return self.classify_bgr(grid)                    # index = b<<10 | g<<5 | r (5 bits each)

    def labels(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Per-pixel class index image (uint8, UNKNOWN = 255) via the lookup table."""
        q = (frame_bgr >> SHIFT).astype(np.int32)
        idx = (q[..., 0] << (2 * BITS)) | (q[..., 1] << BITS) | q[..., 2]
        return self.lut[idx]

    def index(self, name: str):
        return self.classes.index(name) if name in self.classes else None

    def confusion(self, samples: dict) -> dict:
        """{class: {predicted_class_or_'unknown': fraction}} on the sampled pixels (via the lookup table)."""
        out = {}
        for name in self.classes:
            px = samples.get(name)
            if px is None or not len(px):
                continue
            pred = self.labels(np.asarray(px, np.uint8).reshape(-1, 1, 3)).ravel()
            row = {}
            for i, other in enumerate(self.classes):
                row[other] = float(np.mean(pred == i))
            row["unknown"] = float(np.mean(pred == UNKNOWN))
            out[name] = row
        return out


def format_confusion(conf: dict) -> str:
    cols = list(next(iter(conf.values())).keys()) if conf else []
    lines = ["sampled ->  " + " ".join(f"{c[:8]:>8}" for c in cols)]
    for name, row in conf.items():
        lines.append(f"{name[:10]:>10}  " + " ".join(f"{row[c] * 100:7.1f}%" for c in cols))
    return "\n".join(lines)


def load_samples(path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    with np.load(path) as data:
        return {k: data[k] for k in data.files}


def save_samples(samples: dict, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **{k: np.asarray(v, np.uint8) for k, v in samples.items() if len(v)})
    return path
