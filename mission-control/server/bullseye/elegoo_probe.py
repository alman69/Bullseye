"""No-motion diagnostic for the preserved ELEGOO V4.0 TCP/UART bridge.

Run this file directly with Python 3; no pip packages or Internet are needed.
The installed firmware is not assumed to match the repository until tested.
Only N100 (stop) and N21 (ultrasonic query) can be sent by this tool.
"""

import argparse
from collections import deque
import json
import socket
import sys
import time
import uuid


class ProbeError(RuntimeError):
    pass


class ReplyTimeout(ProbeError):
    pass


class FrameParser:
    """Parse flat brace-delimited vendor replies, not newline-delimited JSON."""

    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[str]:
        frames = []
        for value in data:
            if value == ord("{"):
                self.buffer = bytearray(b"{")
            elif self.buffer:
                self.buffer.append(value)
                if len(self.buffer) > 1024:
                    self.buffer.clear()
                    raise ProbeError("Oversized response from the command bridge")
                if value == ord("}"):
                    frames.append(self.buffer.decode("ascii", errors="replace"))
                    self.buffer.clear()
        return frames


class FactoryConnection:
    def __init__(self, host: str, port: int, timeout: float, verbose: bool = False):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.verbose = verbose
        self.sock = None
        self.parser = FrameParser()
        self.pending = deque()
        self.heartbeats = 0
        self.received_bytes = 0
        self.received_frames = deque(maxlen=12)
        self.unmatched_frames = deque(maxlen=12)

    def __enter__(self):
        self.sock = socket.create_connection((self.host, self.port), self.timeout)
        print(f"TCP connected: {self.host}:{self.port}", flush=True)
        return self

    def __exit__(self, *_):
        # Never reconnect or replay commands. Try STOP before closing, even if
        # the sensor query timed out, failed, or was interrupted with Ctrl-C.
        if self.sock is not None:
            try:
                self.send({"N": 100, "H": "exit"})
            except OSError:
                pass
            finally:
                self.sock.close()
                self.sock = None
                self.print_diagnostics()

    def print_diagnostics(self):
        print(
            f"Received: {self.received_bytes} bytes; "
            f"ESP32 heartbeat messages echoed: {self.heartbeats}",
            flush=True,
        )
        if self.received_frames:
            print(f"Recent complete replies: {list(self.received_frames)!r}", flush=True)
        if self.unmatched_frames:
            print(f"Unmatched replies: {list(self.unmatched_frames)!r}", flush=True)
        if self.parser.buffer:
            print(f"Incomplete reply: {bytes(self.parser.buffer)!r}", flush=True)
        if self.received_bytes == 0:
            print("TCP opened, but no application data arrived; ESP32 servicing is unverified.", flush=True)
        elif self.heartbeats and not self.received_frames:
            print("ESP32 heartbeat exchange worked; no UNO reply was observed.", flush=True)

    def send(self, command: dict):
        if command.get("N") not in (21, 100):
            raise ValueError("The diagnostic only permits distance queries and STOP")
        data = json.dumps(command, separators=(",", ":")).encode("ascii")
        if self.verbose:
            print(f"TX {data!r}", flush=True)
        self.sock.sendall(data)

    def receive_matching(self, matches, *, timeout=None) -> str:
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while True:
            while self.pending:
                frame = self.pending.popleft()
                if matches(frame):
                    return frame
                self.unmatched_frames.append(frame)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReplyTimeout("No matching UNO reply before the response timeout")
            self.sock.settimeout(min(remaining, 0.2))
            try:
                data = self.sock.recv(2048)
            except socket.timeout:
                continue
            if not data:
                raise ProbeError("ESP32 closed the TCP connection before a matching UNO reply")
            self.received_bytes += len(data)
            if self.verbose:
                print(f"RX {data!r}", flush=True)
            for frame in self.parser.feed(data):
                if frame == "{Heartbeat}":
                    self.sock.sendall(b"{Heartbeat}")
                    self.heartbeats += 1
                else:
                    self.received_frames.append(frame)
                    self.pending.append(frame)

    def stop(self):
        self.send({"N": 100, "H": "stop"})
        self.receive_matching(lambda frame: frame == "{ok}")

    def distance(self) -> int:
        request_id = "p" + uuid.uuid4().hex[:8]
        prefix = "{" + request_id + "_"
        self.send({"N": 21, "D1": 2, "H": request_id})
        frame = self.receive_matching(lambda reply: reply.startswith(prefix))
        value = frame[len(prefix):-1]
        if not value.isascii() or not value.isdigit():
            raise ProbeError("UNO replied to the distance query with a non-numeric value")
        distance_cm = int(value)
        # This exact vendor driver clamps its output to 150 cm. Other values
        # mean that we have not established compatibility with this source.
        if not 0 <= distance_cm <= 150:
            raise ProbeError("Distance reply is outside this vendor driver's 0–150 cm range")
        return distance_cm


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("probe", "stop"), nargs="?", default="probe")
    parser.add_argument("--host", default="192.168.4.1")
    parser.add_argument("--port", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--verbose", action="store_true", help="Show raw sent commands and received bytes")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535 or not 0.1 <= args.timeout <= 10:
        parser.error("port must be 1–65535 and timeout must be 0.1–10 seconds")

    print("No-motion test: STOP and ultrasonic query only; firmware is unchanged.", flush=True)
    try:
        with FactoryConnection(args.host, args.port, args.timeout, args.verbose) as connection:
            stop_acknowledged = False
            try:
                connection.stop()
                stop_acknowledged = True
                print("UNO returned {ok} for STOP (software acknowledgement; inspect wheels).", flush=True)
            except ReplyTimeout:
                if args.action == "stop":
                    raise
                print("STOP was sent but not acknowledged; stopping is UNVERIFIED.", flush=True)
                print("Trying only the distance query to check the UNO reply path.", flush=True)
            if args.action == "probe":
                distance_cm = connection.distance()
                print("Matched a unique ultrasonic-query reply from the UNO.", flush=True)
                if distance_cm == 0:
                    print("Distance: 0 cm — INVALID / no echo; this does not mean clear space.")
                elif distance_cm == 150:
                    print("Distance: 150 cm — vendor-clamped reading, not an exact long-range measurement.")
                else:
                    print(f"Distance: {distance_cm} cm — compare with the physical target distance.")
                if not stop_acknowledged:
                    print("Sensor reply matched, but STOP acknowledgement is still UNVERIFIED.", flush=True)
        print("Sent a final best-effort STOP and closed the connection. No movement was requested.")
        return 0
    except KeyboardInterrupt:
        print("Interrupted. STOP was attempted if connected; inspect the rover.", file=sys.stderr)
        return 130
    except (OSError, ProbeError) as error:
        print(f"Test incomplete: {error}", file=sys.stderr)
        print("No movement was requested. TCP alone does not verify the UNO bridge.", file=sys.stderr)
        print("Check ELEGOO Wi-Fi, Cam switch, rover power, and that the phone app is closed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
