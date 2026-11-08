"""Tests for the MJPEG parser and CameraStream using a fake local ESP32 server.

The fake server mimics firmware app_httpd.cpp: HTTP/1.1 chunked response,
multipart parts with Content-Length, and NO boundary before the first part.
"""
from __future__ import annotations

import http.server
import tempfile
import threading
import time
import unittest
from pathlib import Path

import cv2
import numpy as np

from camera import CameraStream, FileSource, MjpegParser, RateMeter

BOUNDARY = b"123456789000000000000987654321"


def make_jpeg(value: int, size=(48, 64)) -> bytes:
    img = np.full((size[0], size[1], 3), value, np.uint8)
    cv2.putText(img, str(value), (5, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255 - value,) * 3, 2)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def esp32_part(jpeg: bytes) -> bytes:
    return (b"Content-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpeg)) + jpeg + b"\r\n--" + BOUNDARY + b"\r\n"


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.jpegs = [make_jpeg(v) for v in (20, 120, 220)]
        self.stream = b"".join(esp32_part(j) for j in self.jpegs)

    def test_esp32_format_whole(self):
        self.assertEqual(MjpegParser().feed(self.stream), self.jpegs)

    def test_esp32_format_split_reads(self):
        for step in (1, 7, 333, 4096):
            with self.subTest(step=step):
                parser, out = MjpegParser(), []
                for i in range(0, len(self.stream), step):
                    out += parser.feed(self.stream[i:i + step])
                self.assertEqual(out, self.jpegs)

    def test_without_content_length_uses_markers(self):
        data = b"".join(b"--" + BOUNDARY + b"\r\nContent-Type: image/jpeg\r\n\r\n" + j + b"\r\n" for j in self.jpegs)
        parser, out = MjpegParser(), []
        for i in range(0, len(data), 100):
            out += parser.feed(data[i:i + 100])
        self.assertEqual(out, self.jpegs)

    def test_resyncs_after_garbage(self):
        out = MjpegParser().feed(b"junk\x00\x01garbage" * 50 + self.stream)
        self.assertEqual(out, self.jpegs)


class RateMeterTests(unittest.TestCase):
    def test_rate(self):
        m = RateMeter(window_s=2.0)
        for i in range(21):
            m.tick(100.0 + i * 0.1)        # 10 Hz
        self.assertAlmostEqual(m.rate(now=102.0), 10.0, places=5)
        self.assertEqual(m.rate(now=110.0), 0.0)   # stale -> 0


class FakeEsp32(http.server.ThreadingHTTPServer):
    """Each connection runs the next scripted session: (n_frames, then 'close' | 'stall')."""
    daemon_threads = True

    def __init__(self, sessions, fps=50):
        self.sessions = list(sessions)
        self.fps = fps
        self.connections = 0
        self.lock = threading.Lock()
        self.release = threading.Event()
        super().__init__(("127.0.0.1", 0), Handler)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        srv = self.server
        with srv.lock:
            idx = srv.connections
            srv.connections += 1
        n_frames, end = srv.sessions[idx] if idx < len(srv.sessions) else (10_000, "close")
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace;boundary=" + BOUNDARY.decode())
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            for i in range(n_frames):
                part = esp32_part(make_jpeg((idx * 40 + i * 10) % 256))
                for piece in (part[:50], part[50:]):   # split like httpd_resp_send_chunk calls
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(piece), piece))
                self.wfile.flush()
                time.sleep(1.0 / srv.fps)
            if end == "stall":
                srv.release.wait(5)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


class CameraStreamTests(unittest.TestCase):
    def start_server(self, sessions, fps=50):
        srv = FakeEsp32(sessions, fps)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.addCleanup(srv.release.set)
        return srv, f"http://127.0.0.1:{srv.server_address[1]}/stream"

    def wait_until(self, cond, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(0.02)
        return False

    def test_receives_frames_and_reconnects_after_close(self):
        srv, url = self.start_server([(5, "close"), (5, "close"), (1000, "close")])
        cam = CameraStream(url, timeout_s=1.0, reconnect_min_s=0.05, reconnect_max_s=0.1).start()
        self.addCleanup(cam.stop)
        self.assertTrue(self.wait_until(lambda: cam.connects >= 3 and cam.state == "streaming"))
        self.assertGreaterEqual(cam.disconnects, 2)
        frame = cam.wait_for_frame(-1, timeout=2.0)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.image.shape, (48, 64, 3))
        self.assertFalse(frame.image.flags.writeable)
        self.assertEqual(cam.decode_errors, 0)
        self.assertGreater(cam.stream_fps(), 5)

    def test_detects_stalled_stream_and_reconnects(self):
        srv, url = self.start_server([(3, "stall"), (1000, "close")])
        cam = CameraStream(url, timeout_s=0.4, reconnect_min_s=0.05).start()
        self.addCleanup(cam.stop)
        self.assertTrue(self.wait_until(lambda: cam.connects >= 2, timeout=4.0))
        self.assertIn("timed out", cam.last_error.lower())

    def test_wait_for_frame_returns_newest_only(self):
        srv, url = self.start_server([(1000, "close")], fps=100)
        cam = CameraStream(url, timeout_s=1.0).start()
        self.addCleanup(cam.stop)
        first = cam.wait_for_frame(-1, timeout=2.0)
        self.assertIsNotNone(first)
        time.sleep(0.3)  # slow consumer: ~30 frames arrive meanwhile
        nxt = cam.wait_for_frame(first.frame_id, timeout=1.0)
        self.assertGreater(nxt.frame_id, first.frame_id + 5)   # skipped stale frames
        self.assertEqual(nxt.frame_id, cam.latest().frame_id)
        self.assertLess(time.monotonic() - nxt.received_at, 0.1)

    def test_unreachable_camera_keeps_retrying(self):
        cam = CameraStream("http://127.0.0.1:9/stream", timeout_s=0.3, reconnect_min_s=0.05, reconnect_max_s=0.1).start()
        self.addCleanup(cam.stop)
        self.assertIsNone(cam.wait_for_frame(-1, timeout=0.5))
        self.assertTrue(self.wait_until(lambda: cam.disconnects >= 2))
        self.assertTrue(cam.last_error)

    def test_stop_is_prompt(self):
        srv, url = self.start_server([(1000, "close")])
        cam = CameraStream(url, timeout_s=1.0).start()
        cam.wait_for_frame(-1, timeout=2.0)
        t0 = time.monotonic()
        cam.stop()
        self.assertLess(time.monotonic() - t0, 1.5)
        self.assertEqual(cam.state, "stopped")


    def test_stop_is_prompt_when_camera_stalls(self):
        srv, url = self.start_server([(1, "stall")])
        cam = CameraStream(url, timeout_s=3.0).start()
        self.assertIsNotNone(cam.wait_for_frame(-1, timeout=2.0))
        t0 = time.monotonic()
        cam.stop()   # the reader is blocked in a read with a 3 s timeout
        self.assertLess(time.monotonic() - t0, 1.5)


class FileSourceTests(unittest.TestCase):
    def test_folder_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(2):
                Path(tmp, f"img{i}.jpg").write_bytes(make_jpeg(50 + i * 100))
            src = FileSource(tmp, fps=50).start()
            try:
                f1 = src.wait_for_frame(-1, timeout=1.0)
                self.assertEqual(src.current_name, "img0.jpg")
                src.next_image()
                self.assertEqual(src.current_name, "img1.jpg")
                f2 = src.wait_for_frame(f1.frame_id, timeout=1.0)
                self.assertGreater(f2.frame_id, f1.frame_id)
            finally:
                src.stop()


if __name__ == "__main__":
    unittest.main()
